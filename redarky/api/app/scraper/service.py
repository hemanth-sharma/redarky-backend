"""
app/scraper/service.py

Builds the shared scraper payload and calls the Go scraper.

SHARED SCRAPING MODEL:
  1. Aggregate all unique "include"+"brand" keywords across active projects
  2. Aggregate all active MonitoredSource rows (subscriber_count > 0)
  3. Build ONE payload and send to Go scraper
  4. Go scraper does both:
     - Reddit global search for each keyword (sorted by new)
     - Pull last N posts from each subreddit
  5. Go scraper returns items → ingestion service bulk-upserts them

This means N projects watching "Notion" + r/SaaS all share ONE Go call.
"""
import logging
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.keywords.service import get_active_include_keywords
from app.sources.service import get_active_sources_for_scraper, mark_source_scraped
from app.scraper.models import ScraperRun
from app.scraper.schemas import (
    ScraperPayload, GoScrapeResult, ScrapedItem, ScraperRunResponse
)
from app.utils.exceptions import DomainException

logger = logging.getLogger("uvicorn.scraper")


async def build_shared_payload(db: AsyncSession) -> ScraperPayload:
    """
    Builds the shared scraper payload from ALL active projects.

    Keywords: deduped across projects (a keyword used by 5 projects is
              sent to Go ONCE).
    Subreddits: each active MonitoredSource row is one entry, regardless
                of how many projects share it.
    """
    keywords = await get_active_include_keywords(db)
    sources = await get_active_sources_for_scraper(db)

    # Reddit subreddits only for now — other source types will need
    # their own payload shape later.
    subreddits = [
        s.identifier for s in sources
        if s.source_type == "reddit" and s.identifier
    ]

    return ScraperPayload(
        keywords=keywords,
        subreddits=subreddits,
        sort="new",
        since_timestamp=None,  # let Go decide default lookback
        include_comments=True,
    )


async def create_scraper_run(db: AsyncSession, payload: ScraperPayload) -> ScraperRun:
    """Create a ScraperRun row BEFORE calling Go, so we can track
    runs that never completed (Go crashed, network down, etc.)."""
    run = ScraperRun(
        status="running",
        payload_sent=payload.model_dump(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def update_scraper_run_status(
    db: AsyncSession,
    run: ScraperRun,
    status: str,
    total_items: int = 0,
    new_items: int = 0,
    dup_items: int = 0,
    error: str | None = None,
) -> ScraperRun:
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.total_items_pulled = total_items
    run.new_items_inserted = new_items
    run.duplicate_items_skipped = dup_items
    if error:
        run.error_message = error[:1024]
    await db.commit()
    await db.refresh(run)
    return run


async def call_go_scraper(payload: ScraperPayload) -> GoScrapeResult:
    """
    Hits the Go scraper's /scrape endpoint and returns the parsed result.
    Raises DomainException on network/HTTP errors so the worker can
    catch and update the ScraperRun row.
    """
    url = settings.GO_SCRAPER_URL
    if not url.endswith("/scrape"):
        url = f"{url}/scrape"

    try:
        async with httpx.AsyncClient() as client:
            logger.info("Calling Go scraper at %s with %d keywords, %d subreddits",
                        url, len(payload.keywords), len(payload.subreddits))
            print("payload = ", payload)
            res = await client.post(url, json=payload.model_dump(), timeout=120.0)
            res.raise_for_status()
            data = res.json()
            if data is None:
                logger.warning("Go scraper returned null body")
                return GoScrapeResult()
            return GoScrapeResult.model_validate(data)
    except httpx.HTTPStatusError as e:
        logger.error("Go scraper HTTP %d: %s", e.response.status_code, e.response.text[:500])
        raise DomainException(f"Go scraper HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        logger.error("Failed to call Go scraper: %s", str(e))
        raise DomainException(f"Go scraper unreachable: {str(e)[:200]}")


async def run_shared_scrape(db: AsyncSession) -> ScraperRun:
    """
    Orchestrator: builds payload, calls Go, records the ScraperRun.
    Does NOT process the returned items — that's the ingestion
    service's job (called by the worker after this returns).

    Worker flow (Celery Beat every 30 min):
      1. run_shared_scrape(db)  →  ScraperRun + items list
      2. ingestion.bulk_upsert(db, items, run.id)  →  new_raw_post_ids
      3. matching.run_pipeline(db, new_raw_post_ids)  →  creates MatchedPosts + Leads
    """
    # 1. Build payload
    payload = await build_shared_payload(db)
    if not payload.keywords and not payload.subreddits:
        logger.info("No active keywords/sources — skipping scraper run")
        run = await create_scraper_run(db, payload)
        return await update_scraper_run_status(db, run, status="success", total_items=0)

    # 2. Create ScraperRun row
    run = await create_scraper_run(db, payload)

    # 3. Call Go scraper
    try:
        result: GoScrapeResult = await call_go_scraper(payload)
    except DomainException as e:
        return await update_scraper_run_status(db, run, status="failed", error=str(e))

    # 4. Update ScraperRun with totals (final new/dup counts come from ingestion)
    await update_scraper_run_status(
        db, run,
        status="success" if not result.errors else "partial",
        total_items=len(result.items),
    )

    # 5. Mark all touched sources as scraped (for last_scraped_at ordering)
    for subreddit in payload.subreddits:
        # We'd need source_id here; for MVP, skip per-source update.
        # The mark_source_scraped helper is available if needed.
        pass

    # Return the run + items (worker handles ingestion)
    run._items = result.items  # type: ignore[attr-defined]  # stash for worker
    return run


async def get_scraper_run(db: AsyncSession, run_id) -> ScraperRun:
    from sqlalchemy import select
    result = await db.execute(select(ScraperRun).where(ScraperRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        from app.utils.exceptions import NotFoundException
        raise NotFoundException(f"ScraperRun {run_id} not found")
    return run


async def list_scraper_runs(db: AsyncSession, limit: int = 50) -> list[ScraperRun]:
    from sqlalchemy import select
    result = await db.execute(
        select(ScraperRun).order_by(ScraperRun.started_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
