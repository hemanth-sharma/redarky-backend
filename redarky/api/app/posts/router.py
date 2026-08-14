"""
app/posts/router.py

User-facing feed of MatchedPosts.

  GET /posts                              → paginated feed with filters
  GET /posts/{id}                         → post detail
  GET /posts/{id}/highlights              → every KeywordMatch for highlighting

Users CANNOT manually create/update/delete matched posts — they are produced
by the matching pipeline. All mutations happen via background workers.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.posts.schemas import (
    MatchedPostResponse, MatchedPostListResponse, MatchedPostFilter,
    PostHighlightsResponse,
)
from app.posts import service
from app.utils.exceptions import NotFoundException

router = APIRouter(tags=["Posts"])


@router.get("/posts", response_model=MatchedPostListResponse)
async def read_posts(
    project_id: uuid.UUID | None = None,
    source: str | None = None,
    search: str | None = None,
    subreddit: str | None = None,
    min_intent_score: float | None = None,
    is_brand_mention: bool | None = None,
    is_lead: bool | None = None,
    lead_status: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "intent_score",
    sort_desc: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns a paginated list of matched posts for the current user.

    Filters:
      - project_id: scope to a specific project
      - source: "reddit" / "x" / ...
      - search: substring match on title/content
      - subreddit: exact subreddit match
      - min_intent_score: only posts with score ≥ this
      - is_brand_mention: only brand-mention posts
      - is_lead: only posts that became Leads
      - lead_status: filter by Lead status ("new" / "contacted" / ...)

    Sort:
      - sort_by: "intent_score" | "created_at" | "score"
      - sort_desc: true (default) = highest first
    """
    filters = MatchedPostFilter(
        project_id=project_id,
        source=source,
        search=search,
        subreddit=subreddit,
        min_intent_score=min_intent_score,
        is_brand_mention=is_brand_mention,
        is_lead=is_lead,
        lead_status=lead_status,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
    return await service.get_matched_posts(db=db, filters=filters, owner_id=current_user.id)


@router.get("/posts/{id}", response_model=MatchedPostResponse)
async def read_post(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await service.get_matched_post(db=db, post_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/posts/{id}/highlights", response_model=PostHighlightsResponse)
async def post_highlights(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns every KeywordMatch for this post — the frontend uses this to
    highlight every matched keyword/snippet inside the post body.
    """
    try:
        highlights = await service.get_post_highlights(
            db=db, post_id=id, owner_id=current_user.id
        )
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)

    return PostHighlightsResponse(post_id=id, highlights=highlights)
