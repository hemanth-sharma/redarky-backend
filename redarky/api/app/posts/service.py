"""
app/posts/service.py

Read-side service for the user-facing feed.

Frontend flow:
  - GET /posts?project_id=...&min_intent_score=0.7 → feed list
  - GET /posts/{id}                                 → detail view
  - GET /posts/{id}/highlights                      → every match for highlight rendering
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, desc, asc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MatchedPost, KeywordMatch, Lead
from app.posts.schemas import MatchedPostFilter, MatchedPostListResponse, MatchedPostResponse
from app.utils.exceptions import NotFoundException

logger = logging.getLogger("uvicorn.posts")


async def get_matched_posts(
    db: AsyncSession,
    filters: MatchedPostFilter,
    owner_id: UUID,
) -> MatchedPostListResponse:
    """
    Returns paginated matched posts for the frontend feed.
    Filters by project ownership (owner_id) for security.
    """
    # Build base query with project ownership join
    from app.models import Project
    base = (
        select(MatchedPost)
        .join(Project, MatchedPost.project_id == Project.id)
        .where(Project.owner_id == owner_id)
    )
    count_base = (
        select(func.count(MatchedPost.id))
        .join(Project, MatchedPost.project_id == Project.id)
        .where(Project.owner_id == owner_id)
    )

    # ── Apply filters ───────────────────────────────────────────────────────
    if filters.project_id:
        base = base.where(MatchedPost.project_id == filters.project_id)
        count_base = count_base.where(MatchedPost.project_id == filters.project_id)
    if filters.source:
        base = base.where(MatchedPost.source == filters.source)
        count_base = count_base.where(MatchedPost.source == filters.source)
    if filters.search:
        pattern = f"%{filters.search}%"
        base = base.where(or_(MatchedPost.title.ilike(pattern), MatchedPost.content.ilike(pattern)))
        count_base = count_base.where(or_(MatchedPost.title.ilike(pattern), MatchedPost.content.ilike(pattern)))
    if filters.subreddit:
        base = base.where(MatchedPost.subreddit == filters.subreddit)
        count_base = count_base.where(MatchedPost.subreddit == filters.subreddit)
    if filters.min_intent_score is not None:
        base = base.where(MatchedPost.intent_score >= filters.min_intent_score)
        count_base = count_base.where(MatchedPost.intent_score >= filters.min_intent_score)
    if filters.is_brand_mention is not None:
        base = base.where(MatchedPost.is_brand_mention == filters.is_brand_mention)
        count_base = count_base.where(MatchedPost.is_brand_mention == filters.is_brand_mention)
    if filters.is_lead is not None:
        base = base.where(MatchedPost.is_lead == filters.is_lead)
        count_base = count_base.where(MatchedPost.is_lead == filters.is_lead)

    # ── Sort ────────────────────────────────────────────────────────────────
    sort_col = {
        "intent_score": MatchedPost.intent_score,
        "created_at": MatchedPost.created_at,
        "score": MatchedPost.score,
    }.get(filters.sort_by, MatchedPost.intent_score)
    base = base.order_by(desc(sort_col) if filters.sort_desc else asc(sort_col))

    # ── Paginate ────────────────────────────────────────────────────────────
    offset = (filters.page - 1) * filters.page_size
    base = base.offset(offset).limit(filters.page_size)

    # ── Execute ─────────────────────────────────────────────────────────────
    total = await db.scalar(count_base) or 0
    result = await db.execute(base)
    posts = result.scalars().all()

    # ── Attach lead_status denormalized ─────────────────────────────────────
    post_ids = [p.id for p in posts]
    leads_map: dict[UUID, str] = {}
    if post_ids:
        leads = (await db.execute(
            select(Lead.matched_post_id, Lead.status).where(Lead.matched_post_id.in_(post_ids))
        )).all()
        leads_map = {row[0]: row[1] for row in leads}

    items = []
    for p in posts:
        item = MatchedPostResponse.model_validate(p)
        item.lead_status = leads_map.get(p.id)
        items.append(item)

    return MatchedPostListResponse(
        items=items,
        total=int(total),
        page=filters.page,
        page_size=filters.page_size,
    )


async def get_matched_post(
    db: AsyncSession, post_id: UUID, owner_id: UUID
) -> MatchedPostResponse:
    from app.models import Project
    result = await db.execute(
        select(MatchedPost)
        .join(Project, MatchedPost.project_id == Project.id)
        .where(MatchedPost.id == post_id, Project.owner_id == owner_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise NotFoundException(f"Post {post_id} not found")

    resp = MatchedPostResponse.model_validate(post)

    # Attach lead status if any
    lead = await db.scalar(
        select(Lead.status).where(Lead.matched_post_id == post_id)
    )
    resp.lead_status = lead
    return resp


async def get_post_highlights(
    db: AsyncSession, post_id: UUID, owner_id: UUID
) -> list[dict]:
    """
    Returns every KeywordMatch for this post — frontend uses this to
    highlight every matched keyword inside the post body.
    """
    from app.models import Project
    # Verify ownership + get the matched_post's raw_post_id in one query
    result = await db.execute(
        select(MatchedPost)
        .join(Project, MatchedPost.project_id == Project.id)
        .where(MatchedPost.id == post_id, Project.owner_id == owner_id)
    )
    mp = result.scalar_one_or_none()
    if not mp:
        raise NotFoundException(f"Post {post_id} not found")

    # Load every KeywordMatch that hit on the underlying raw_post
    matches = (await db.execute(
        select(KeywordMatch, Keyword.keyword)
        .join(Keyword, KeywordMatch.keyword_id == Keyword.id)
        .where(KeywordMatch.raw_post_id == mp.raw_post_id)
    )).all()

    return [
        {
            "matched_keyword": row[1],
            "matched_snippet": row[0].matched_snippet,
            "intent_phrase": mp.matched_intent_phrase,
            "is_brand_mention": mp.is_brand_mention,
        }
        for row in matches
    ]


async def get_post_by_id(db: AsyncSession, post_id: UUID) -> MatchedPost | None:
    """Internal helper."""
    result = await db.execute(select(MatchedPost).where(MatchedPost.id == post_id))
    return result.scalar_one_or_none()
