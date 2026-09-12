"""
app/sources/service.py

Manages the global MonitoredSource registry and the ProjectSource join table.

NO APIFY: All Apify coupling has been removed. Scheduling is handled by
Celery Beat, which calls the Go scraper directly with the shared payload
(see app/scraper/service.py).

The shared-scraping logic:
  - When user A adds r/SaaS, MonitoredSource row is created with
    subscriber_count=1, is_active=True.
  - When user B adds r/SaaS, subscriber_count becomes 2 (no new source row).
  - When either removes it, subscriber_count decreases.
  - When subscriber_count hits 0, is_active=False → Celery Beat skips it.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, and_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonitoredSource, ProjectSource
from app.sources.schemas import SourceAddRequest, SourceType
from app.utils.exceptions import NotFoundException

logger = logging.getLogger("uvicorn.sources")


async def add_source_to_project(
    db: AsyncSession,
    project_id: UUID,
    source_in: SourceAddRequest,
) -> MonitoredSource:
    """
    Links a source to a user's project.

    Steps:
      1. Get or create the global MonitoredSource row
      2. Create ProjectSource link if not exists
      3. Increment subscriber_count
      4. If subscriber_count went 0 → 1, flip is_active=True
    """
    # Normalize identifier (lowercase for reddit subreddits)
    identifier = source_in.identifier.lower().strip()

    # Step 1: get or create
    result = await db.execute(
        select(MonitoredSource).where(
            and_(
                MonitoredSource.source_type == source_in.source_type.value,
                MonitoredSource.identifier == identifier,
            )
        )
    )
    source = result.scalar_one_or_none()

    if source is None:
        source = MonitoredSource(
            source_type=source_in.source_type.value,
            identifier=identifier,
            interval_minutes=source_in.interval_minutes,
            subscriber_count=0,
            is_active=False,  # will flip to True below
        )
        db.add(source)
        await db.flush()  # get the ID

    # Step 2: link project to source (if not already linked)
    existing_link = await db.execute(
        select(ProjectSource).where(
            and_(
                ProjectSource.project_id == project_id,
                ProjectSource.monitored_source_id == source.id,
            )
        )
    )
    if not existing_link.scalar_one_or_none():
        link = ProjectSource(
            project_id=project_id,
            monitored_source_id=source.id,
        )
        db.add(link)
        # Step 3: increment subscriber count
        source.subscriber_count += 1
        # Step 4: flip active if this is the first subscriber
        if source.subscriber_count == 1:
            source.is_active = True
            logger.info("Activated source %s/%s (first subscriber)", source.source_type, source.identifier)

    await db.commit()
    await db.refresh(source)
    return source


async def remove_source_from_project(
    db: AsyncSession,
    project_id: UUID,
    source_type: str,
    identifier: str,
) -> None:
    """
    Removes a source from a project. If subscriber_count hits 0, the
    source is deactivated (is_active=False) so Celery Beat stops
    including it in scraper batches.
    """
    identifier = identifier.lower().strip()

    source_result = await db.execute(
        select(MonitoredSource).where(
            and_(
                MonitoredSource.source_type == source_type,
                MonitoredSource.identifier == identifier,
            )
        )
    )
    source = source_result.scalar_one_or_none()
    if not source:
        return

    # Remove the project link
    link_result = await db.execute(
        select(ProjectSource).where(
            and_(
                ProjectSource.project_id == project_id,
                ProjectSource.monitored_source_id == source.id,
            )
        )
    )
    link = link_result.scalar_one_or_none()
    if link:
        await db.delete(link)
        source.subscriber_count = max(0, source.subscriber_count - 1)

        # Auto-deactivate when nobody is watching
        if source.subscriber_count == 0:
            source.is_active = False
            logger.info(
                "Deactivated source %s/%s (no subscribers)",
                source.source_type,
                source.identifier,
            )

    await db.commit()


async def get_project_sources(db: AsyncSession, project_id: UUID) -> list[MonitoredSource]:
    """Returns all sources a project is watching."""
    stmt = (
        select(MonitoredSource)
        .join(ProjectSource, ProjectSource.monitored_source_id == MonitoredSource.id)
        .where(ProjectSource.project_id == project_id)
        .order_by(MonitoredSource.identifier)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Scraper-facing: get all active sources for shared batch ──────────────────
async def get_active_sources_for_scraper(db: AsyncSession, platform: str | None = None) -> list[MonitoredSource]:
    """
    Returns all MonitoredSource rows where is_active=True.
    Used by the scraper service to build the shared Go scraper payload.

    Platform-aware: when `platform` is given, only sources of that type
    linked to active projects that include the platform in their
    `platforms` config are returned (per-product platform selection).

    Each row is included ONCE per batch — multiple projects watching the
    same subreddit share the same scrape.
    """
    from app.models import Project

    stmt = (
        select(MonitoredSource)
        .join(ProjectSource, ProjectSource.monitored_source_id == MonitoredSource.id)
        .join(Project, Project.id == ProjectSource.project_id)
        .where(
            MonitoredSource.is_active == True,  # noqa: E712
            Project.is_pipeline_active == True,  # noqa: E712
        )
    )
    if platform:
        stmt = stmt.where(
            (MonitoredSource.source_type == platform)
            & cast(Project.platforms, String).ilike(f'%"{platform}"%')
        )
    stmt = stmt.distinct().order_by(MonitoredSource.last_scraped_at.asc().nullsfirst())

    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


async def mark_source_scraped(db: AsyncSession, source_id: UUID) -> None:
    """Called by the scraper service after a batch completes."""
    result = await db.execute(
        select(MonitoredSource).where(MonitoredSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if source:
        source.last_scraped_at = datetime.now(timezone.utc)
        await db.commit()
