"""
app/projects/service.py

Project CRUD + pipeline activation/deactivation.

When a user activates their pipeline:
  - Sets is_pipeline_active = True
  - Sets pipeline_activated_at = now()
  - Celery Beat picks this up on its next 30-min cycle and includes
    this project's keywords + sources in the next shared scraper batch.

When deactivated:
  - Sets is_pipeline_active = False
  - The project's keywords/sources are NOT removed from the global
    MonitoredSource registry (other projects may share them).
  - subscriber_count on shared sources is NOT decremented (the link
    is still there; the project is just paused).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Project, Keyword, MonitoredSource, ProjectSource, MatchedPost, Lead, ScraperRun
)
from app.projects.schemas import ProjectCreate, ProjectUpdate, ProjectStats
from app.utils.exceptions import NotFoundException


async def get_projects(db: AsyncSession, owner_id: UUID) -> list[Project]:
    result = await db.execute(
        select(Project).where(Project.owner_id == owner_id).order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


async def get_project(db: AsyncSession, project_id: UUID, owner_id: UUID) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundException(f"Project {project_id} not found")
    return project


async def create_project(db: AsyncSession, project_in: ProjectCreate, owner_id: UUID) -> Project:
    project = Project(**project_in.model_dump(), owner_id=owner_id)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


async def update_project(
    db: AsyncSession, project: Project, project_in: ProjectUpdate
) -> Project:
    for field, value in project_in.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, project: Project) -> None:
    await db.delete(project)
    await db.commit()


# ── Pipeline activation ──────────────────────────────────────────────────────
async def activate_pipeline(db: AsyncSession, project: Project) -> Project:
    project.is_pipeline_active = True
    project.pipeline_activated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    return project


async def deactivate_pipeline(db: AsyncSession, project: Project) -> Project:
    project.is_pipeline_active = False
    await db.commit()
    await db.refresh(project)
    return project


# ── Dashboard stats ──────────────────────────────────────────────────────────
async def get_project_stats(db: AsyncSession, project: Project) -> ProjectStats:
    """Aggregated counts for the project dashboard."""
    project_id = project.id

    matched_count = await db.scalar(
        select(func.count(MatchedPost.id)).where(MatchedPost.project_id == project_id)
    )
    leads_count = await db.scalar(
        select(func.count(Lead.id)).where(Lead.project_id == project_id)
    )
    unactioned_leads = await db.scalar(
        select(func.count(Lead.id)).where(
            Lead.project_id == project_id,
            Lead.status.in_(["new", "reviewed"])
        )
    )
    active_keywords = await db.scalar(
        select(func.count(Keyword.id)).where(Keyword.project_id == project_id)
    )
    active_sources = await db.scalar(
        select(func.count(ProjectSource.id)).where(ProjectSource.project_id == project_id)
    )

    # Last scraper run that touched this project's keywords/sources.
    # For MVP, just grab the most recent successful run globally — proper
    # per-project tracking would require joining payload_sent against
    # project keywords, which is overkill for MVP.
    last_run = await db.scalar(
        select(func.max(ScraperRun.started_at)).where(ScraperRun.status == "success")
    )

    return ProjectStats(
        project_id=project_id,
        matched_posts_count=int(matched_count or 0),
        leads_count=int(leads_count or 0),
        unactioned_leads_count=int(unactioned_leads or 0),
        active_keywords_count=int(active_keywords or 0),
        active_sources_count=int(active_sources or 0),
        last_scraper_run_at=last_run,
    )
