"""
app/workers/tasks.py

Misc Celery tasks — currently just nightly TTL cleanup.

Cleanup deletes:
  - raw_posts where expires_at < now (7-day TTL)
  - matched_posts where expires_at < now (per-project retention)
Cascade deletes propagate to keyword_matches, post_embeddings, leads.
"""
import asyncio
import logging

from app.database import SessionLocal
from app.workers.celery_app import celery_app
from app.ingestion.service import run_full_cleanup
from app.workers.utils import run_async_db_task

logger = logging.getLogger("uvicorn.workers.tasks")


@celery_app.task(name="app.workers.tasks.run_cleanup_task")
def run_cleanup_task():
    """Nightly TTL cleanup. Celery Beat fires this at 3 AM UTC."""
    # asyncio.run(_run_cleanup_async())
    return _run_cleanup_async()


@run_async_db_task
async def _run_cleanup_async(db):
    try:
        result = await run_full_cleanup(db)
        logger.info(
            "Cleanup: %d raw_posts, %d matched_posts deleted",
            result.deleted_raw_posts,
            result.deleted_matched_posts,
        )
    except Exception as e:
        logger.exception("Cleanup task failed: %s", e)
        raise
