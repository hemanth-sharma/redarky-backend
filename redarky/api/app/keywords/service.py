"""
app/keywords/service.py
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keywords.schemas import KeywordCreate, KeywordUpdate, KeywordType
from app.models import Keyword
from app.utils.exceptions import NotFoundException


async def get_keywords(db: AsyncSession, project_id: UUID | None = None) -> list[Keyword]:
    stmt = select(Keyword).order_by(Keyword.created_at.desc())
    if project_id:
        stmt = stmt.where(Keyword.project_id == project_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_keyword(db: AsyncSession, keyword_id: UUID) -> Keyword:
    result = await db.execute(select(Keyword).where(Keyword.id == keyword_id))
    kw = result.scalar_one_or_none()
    if not kw:
        raise NotFoundException(f"Keyword {keyword_id} not found")
    return kw


async def create_keyword(db: AsyncSession, keyword_in: KeywordCreate) -> Keyword:
    # Normalize: lowercase + strip — avoids "Notion" vs "notion" duplicates
    normalized = keyword_in.keyword.lower().strip()
    keyword = Keyword(
        project_id=keyword_in.project_id,
        keyword=normalized,
        keyword_type=keyword_in.keyword_type.value,
    )
    db.add(keyword)
    await db.commit()
    await db.refresh(keyword)
    return keyword


async def update_keyword(
    db: AsyncSession, keyword: Keyword, keyword_in: KeywordUpdate
) -> Keyword:
    for field, value in keyword_in.model_dump(exclude_unset=True).items():
        if field == "keyword":
            value = value.lower().strip()
        if field == "keyword_type":
            value = value.value
        setattr(keyword, field, value)
    await db.commit()
    await db.refresh(keyword)
    return keyword


async def delete_keyword(db: AsyncSession, keyword: Keyword) -> None:
    await db.delete(keyword)
    await db.commit()


# ── Aggregator (used by scraper service to build the shared payload) ─────────
async def get_active_include_keywords(db: AsyncSession, platform: str | None = None) -> list[str]:
    """
    Returns all unique "include" keywords across all projects with
    is_pipeline_active=True. Used by the scraper service to build
    the shared Go scraper payload.

    Brand keywords are also included — they're useful for global search.
    Exclude keywords are NOT included here (they're applied locally
    during Stage 1 matching, not sent to the scraper).
    """
    from app.models import Project

    conditions = [
        Project.is_pipeline_active == True,
        Keyword.keyword_type.in_([
            KeywordType.INCLUDE.value,
            KeywordType.BRAND.value,
        ]),
    ]
    
    platform_cond = _platform_filter(platform)
    if platform_cond is not None:
        conditions.append(platform_cond)

    stmt = (
        select(Keyword.keyword)
        .distinct()
        .select_from(Keyword)
        .join(Project, Keyword.project_id == Project.id)
        .where(*conditions)
    )
    result = await db.execute(stmt)
    return [r[0] for r in result.fetchall()]


def _platform_filter(platform: str | None):
    """SQLAlchemy criterion limiting to projects that pull from `platform`.
    JSON containment compared via text cast so it works on both Postgres
    JSONB and SQLite JSON."""
    if not platform:
        return None
        
    from sqlalchemy import cast, String
    from app.models import Project
    return cast(Project.platforms, String).ilike(f'%"{platform}"%')