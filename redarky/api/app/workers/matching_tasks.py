"""
app/workers/matching_tasks.py

Standalone Celery tasks for re-running individual matching stages.

Used when:
  - Stage 2 model got upgraded → re-score everything
  - Stage 3 LLM was offline → re-process failed posts
  - User changed their project goal → re-score their matched posts
  - LLM threshold was lowered → re-check unprocessed posts

Tasks:
  - run_llm_filter_task       → Beat fires every 10 min
  - run_semantic_rescore_task → manual trigger only (POST /matching/rerun-stage2)
  - run_pipeline_task         → called by ingestion router after each batch
"""
import asyncio
import logging

from app.database import SessionLocal
from app.workers.celery_app import celery_app
from app.matching.service import (
    run_pipeline,
    run_stage2_semantic_score,
    run_stage3_llm_filter,
)

logger = logging.getLogger("uvicorn.workers.matching")


# ── Stage 3 LLM filter (Beat-driven, every 10 min) ───────────────────────────
@celery_app.task(name="app.workers.matching_tasks.run_llm_filter_task")
def run_llm_filter_task():
    """
    Runs Stage 3 on unprocessed high-score matched posts.
    Beat fires this every 10 min — catches posts that scored high after
    the initial pipeline run, and retries failed LLM calls.
    """
    asyncio.run(_run_llm_filter_async())


async def _run_llm_filter_async():
    async with SessionLocal() as db:
        try:
            result = await run_stage3_llm_filter(db, limit=50)
            logger.info(
                "LLM filter: %d processed, %d leads created, %d errors",
                result.matched_posts_processed,
                result.leads_created,
                result.llm_errors,
            )
        except Exception as e:
            logger.exception("LLM filter task failed: %s", e)
            raise


# ── Full pipeline (called by ingestion router after each batch) ──────────────
@celery_app.task(name="app.workers.matching_tasks.run_pipeline_task")
def run_pipeline_task(raw_post_ids: list[str]):
    """
    Runs all 3 stages end-to-end on a batch of newly-ingested raw posts.
    Called by the ingestion router's background task.

    raw_post_ids is a list of UUID strings (Celery serializes to JSON).
    """
    asyncio.run(_run_pipeline_async(raw_post_ids))


async def _run_pipeline_async(raw_post_ids: list[str]):
    import uuid as uuid_mod
    ids = [uuid_mod.UUID(pid) for pid in raw_post_ids]
    async with SessionLocal() as db:
        try:
            result = await run_pipeline(db, ids)
            logger.info(
                "Pipeline complete: stage1=%s stage2=%s stage3=%s",
                result.stage1.model_dump(),
                result.stage2.model_dump(),
                result.stage3.model_dump(),
            )
        except Exception as e:
            logger.exception("Pipeline task failed: %s", e)
            raise


# ── Stage 2 rescore (manual trigger via /matching/rerun-stage2) ──────────────
@celery_app.task(name="app.workers.matching_tasks.run_semantic_rescore_task")
def run_semantic_rescore_task():
    """
    Re-runs Stage 2 on ALL matched posts (not just unscored).
    Use after upgrading the embedding model or changing intent phrases.
    """
    asyncio.run(_run_semantic_rescore_async())


async def _run_semantic_rescore_async():
    from sqlalchemy import update
    from app.models import MatchedPost
    from app.matching.service import BASE_KEYWORD_SCORE

    async with SessionLocal() as db:
        # Reset all scores to base — Stage 2 will re-score them
        await db.execute(
            update(MatchedPost)
            .where(MatchedPost.is_processed_to_lead == False)
            .values(intent_score=BASE_KEYWORD_SCORE)
        )
        await db.commit()

        result = await run_stage2_semantic_score(db, limit=10000)
        logger.info(
            "Rescore: %d posts scored, %d above threshold",
            result.matched_posts_scored,
            result.posts_above_threshold,
        )
