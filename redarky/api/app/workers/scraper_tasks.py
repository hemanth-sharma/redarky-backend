"""
app/workers/scraper_tasks.py

Celery tasks for the shared scraper pipeline.

Two entry points:
  - run_shared_scraper_task  → Celery Beat fires every 30 min
  - run_manual_scrape_task   → triggered by POST /scraper/run

Both run the same flow:
  1. Build shared payload from all active projects
  2. Create a ScraperRun row (status=running)
  3. POST /scrape to Go scraper
  4. Bulk-upsert returned items into raw_posts (dedup)
  5. Update ScraperRun with final counts
  6. Run the 3-stage matching pipeline on new raw_post IDs

The shared scraping model means N projects × M keywords = 1 Go call.
"""
import asyncio
import logging

from app.database import SessionLocal
from app.workers.celery_app import celery_app
from app.scraper.service import run_shared_scrape, update_scraper_run_status
from app.ingestion.service import bulk_upsert_raw_posts
from app.matching.service import run_pipeline

logger = logging.getLogger("uvicorn.workers.scraper")


@celery_app.task(name="app.workers.scraper_tasks.run_shared_scraper_task")
def run_shared_scraper_task():
    """Entry point — Celery Beat fires this every 30 min."""
    asyncio.run(_run_shared_scraper_async())


@celery_app.task(name="app.workers.scraper_tasks.run_manual_scrape_task")
def run_manual_scrape_task(project_id: str | None = None):
    """
    Manually triggered by POST /scraper/run.

    `project_id` is kept for API compatibility but currently ignored — the
    scraper always pulls the shared batch (all active projects). Per-project
    scraping was removed to save API quota.
    """
    asyncio.run(_run_shared_scraper_async())


async def _run_shared_scraper_async():
    async with SessionLocal() as db:
        try:
            # ── 1. Scrape (builds payload, calls Go, creates ScraperRun row) ──
            scraper_run = await run_shared_scrape(db)
            items = getattr(scraper_run, "_items", [])

            if not items:
                logger.info("Scraper run %s: 0 items returned", scraper_run.id)
                return {"scraper_run_id": str(scraper_run.id), "items": 0}

            # ── 2. Ingest (dedup) ─────────────────────────────────────────────
            ingestion_result = await bulk_upsert_raw_posts(
                db=db,
                items=items,
                scraper_run_id=scraper_run.id,
            )

            # ── 3. Update ScraperRun with final counts ────────────────────────
            await update_scraper_run_status(
                db, scraper_run,
                status=scraper_run.status,
                total_items=ingestion_result.total_received,
                new_items=ingestion_result.new_inserted,
                dup_items=ingestion_result.duplicates_skipped,
            )

            logger.info(
                "Scraper run %s: %d received, %d new, %d dupes",
                scraper_run.id,
                ingestion_result.total_received,
                ingestion_result.new_inserted,
                ingestion_result.duplicates_skipped,
            )

            # ── 4. Run 3-stage matching pipeline on new raw posts ─────────────
            if ingestion_result.new_raw_post_ids:
                pipeline_result = await run_pipeline(db, ingestion_result.new_raw_post_ids)
                logger.info(
                    "Pipeline complete: stage1=%s, stage2=%s, stage3=%s",
                    pipeline_result.stage1.model_dump(),
                    pipeline_result.stage2.model_dump(),
                    pipeline_result.stage3.model_dump(),
                )
            else:
                logger.info("No new raw posts to process — skipping pipeline")

            return {
                "scraper_run_id": str(scraper_run.id),
                "items_received": ingestion_result.total_received,
                "new_inserted": ingestion_result.new_inserted,
                "duplicates_skipped": ingestion_result.duplicates_skipped,
            }

        except Exception as e:
            logger.exception("Shared scraper task failed: %s", e)
            raise
