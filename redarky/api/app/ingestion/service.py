"""
app/ingestion/service.py

Bulk-upserts scraped items into raw_posts and manages TTL cleanup.

KEY: Uses PostgreSQL ON CONFLICT DO NOTHING on (source, external_id)
so the same Reddit post returned in two batches is silently skipped.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ingestion.models import RawPost
from app.ingestion.schemas import IngestionResult, CleanupResult
from app.scraper.schemas import ScrapedItem
from app.scraper.models import ScraperRun

logger = logging.getLogger("uvicorn.ingestion")

# Hardcoded TTL — raw data only lives long enough for the matcher
# to process it. 7 days is plenty.
RAW_POST_TTL_DAYS = 7


async def bulk_upsert_raw_posts(
    db: AsyncSession,
    items: Sequence[ScrapedItem],
    scraper_run_id: uuid.UUID | None = None,
) -> IngestionResult:
    """
    Bulk-inserts scraped items into raw_posts with ON CONFLICT DO NOTHING.
    Returns the IDs of newly inserted rows (for the matching engine).
    """
    if not items:
        return IngestionResult(
            scraper_run_id=scraper_run_id,
            total_received=0,
            new_inserted=0,
            duplicates_skipped=0,
            new_raw_post_ids=[],
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=RAW_POST_TTL_DAYS)

    inserted_ids: list[uuid.UUID] = []
    skipped = 0

    # Individual inserts with RETURNING — we need the new IDs.
    # For 100-200 items per batch this is fast enough; if scale becomes
    # an issue, switch to a temp table + bulk upsert + bulk RETURNING.
    for item in items:
        stmt = (
            pg_insert(RawPost)
            .values(
                id=uuid.uuid4(),
                scraper_run_id=scraper_run_id,
                source=item.source,
                external_id=item.external_id,
                title=item.title or "",
                content=item.content or "",
                author=item.author or "unknown",
                url=item.url,
                score=item.score or 0,
                comments_count=item.comments_count or 0,
                post_type=item.post_type or "post",
                subreddit=item.subreddit or "",
                created_at_platform=item.created_at_platform,
                fetched_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=["source", "external_id"]
            )
            .returning(RawPost.id)
        )
        result = await db.execute(stmt)
        new_id = result.scalar_one_or_none()
        if new_id:
            inserted_ids.append(new_id)
        else:
            skipped += 1

    await db.commit()

    logger.info(
        "Ingestion: %d received, %d new, %d dupes (scraper_run=%s)",
        len(items), len(inserted_ids), skipped, scraper_run_id,
    )

    return IngestionResult(
        scraper_run_id=scraper_run_id,
        total_received=len(items),
        new_inserted=len(inserted_ids),
        duplicates_skipped=skipped,
        new_raw_post_ids=inserted_ids,
    )


async def cleanup_expired_raw_posts(db: AsyncSession) -> int:
    """
    Deletes raw_posts rows past their expires_at date.
    Run as a nightly Celery Beat task.

    Cascade deletes keyword_matches + post_embeddings + matched_posts
    that reference deleted raw_posts (via FK ondelete=CASCADE).
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(RawPost).where(RawPost.expires_at < now).returning(RawPost.id)
    )
    deleted = len(result.fetchall())
    await db.commit()
    logger.info("TTL cleanup: deleted %d expired raw_posts", deleted)
    return deleted


async def cleanup_expired_matched_posts(db: AsyncSession) -> int:
    """
    Deletes matched_posts rows past their expires_at date.
    Each MatchedPost's expires_at is set to created_at + project.data_retention_days.
    """
    from app.models import MatchedPost
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(MatchedPost).where(MatchedPost.expires_at < now).returning(MatchedPost.id)
    )
    deleted = len(result.fetchall())
    await db.commit()
    logger.info("TTL cleanup: deleted %d expired matched_posts", deleted)
    return deleted


async def run_full_cleanup(db: AsyncSession) -> CleanupResult:
    """Single entry point for the nightly cleanup cron."""
    raw_deleted = await cleanup_expired_raw_posts(db)
    matched_deleted = await cleanup_expired_matched_posts(db)
    return CleanupResult(
        deleted_raw_posts=raw_deleted,
        deleted_matched_posts=matched_deleted,
    )
