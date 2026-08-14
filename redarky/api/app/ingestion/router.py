"""
app/ingestion/router.py

Receives scraped items from the Go scraper (via webhook) and bulk-inserts
them into raw_posts. Then kicks off the 3-stage matching pipeline in the
background.

This endpoint is the fast path:
  Go scraper finishes → POSTs to /ingestion/reddit → we dedup + insert → run_pipeline

Designed to be:
  - Fast: heavy work (matching, LLM) is offloaded to Celery
  - Idempotent: ON CONFLICT DO NOTHING on (source, external_id)
  - Non-blocking: returns 200 immediately, processes async

Security: validates the X-Redarky-Secret header against
INGESTION_WEBHOOK_SECRET in your .env.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.ingestion import service as ingestion_service
from app.ingestion.schemas import IngestionWebhookPayload
from app.workers.matching_tasks import run_pipeline_task

logger = logging.getLogger("uvicorn.ingestion")

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])


@router.post("/reddit", status_code=status.HTTP_200_OK)
async def ingest_reddit(
    payload: IngestionWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_redarky_secret: str = Header(default=""),
):
    """
    Receives scraped items from the Go scraper.

    Steps:
      1. Validate shared secret
      2. Bulk-insert items into raw_posts (global dedup)
      3. Kick off background matching pipeline task
      4. Return immediately

    The 3-stage matching pipeline (keyword → semantic → LLM) runs in the
    background so this endpoint stays fast.
    """
    # ── Auth ──────────────────────────────────────────────────────────────────
    if x_redarky_secret != settings.INGESTION_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    if not payload.items:
        return {"accepted": 0, "message": "No items in payload"}

    # Parse scraper_run_id (if provided) into UUID
    scraper_run_id = None
    if payload.scraper_run_id:
        try:
            scraper_run_id = uuid.UUID(payload.scraper_run_id)
        except ValueError:
            logger.warning("Invalid scraper_run_id in payload: %s", payload.scraper_run_id)

    # ── Bulk insert into raw_posts ────────────────────────────────────────────
    ingestion_result = await ingestion_service.bulk_upsert_raw_posts(
        db=db,
        items=payload.items,
        scraper_run_id=scraper_run_id,
    )

    logger.info(
        "Reddit ingestion: received=%d new=%d dupes=%d run=%s",
        ingestion_result.total_received,
        ingestion_result.new_inserted,
        ingestion_result.duplicates_skipped,
        scraper_run_id,
    )

    # ── Kick off the 3-stage pipeline in background ───────────────────────────
    # Pass only the new raw_post IDs so the pipeline skips dupes.
    if ingestion_result.new_raw_post_ids:
        background_tasks.add_task(
            _trigger_pipeline,
            raw_post_ids=[str(i) for i in ingestion_result.new_raw_post_ids],
        )

    return {
        "accepted": ingestion_result.new_inserted,
        "skipped_dupes": ingestion_result.duplicates_skipped,
        "scraper_run_id": str(scraper_run_id) if scraper_run_id else None,
    }


async def _trigger_pipeline(raw_post_ids: list[str]):
    """Fires the Celery pipeline task after the HTTP response is sent.

    Uses BackgroundTasks so the response returns immediately, and the Celery
    dispatch happens after. If Celery is down, we log and move on — the next
    Celery Beat cycle will pick up any unprocessed posts via the standalone
    Stage 2 + Stage 3 tasks."""
    try:
        run_pipeline_task.delay(raw_post_ids=raw_post_ids)
    except Exception as e:
        logger.error("Failed to queue pipeline task: %s — will be picked up by Beat cycle", e)
