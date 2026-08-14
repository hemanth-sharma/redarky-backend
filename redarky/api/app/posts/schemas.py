"""
app/posts/schemas.py

Schemas for the user-facing feed (MatchedPost).
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MatchedPostResponse(BaseModel):
    """A single row in the user's feed."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    raw_post_id: UUID

    # Denormalized content
    source: str
    external_id: str
    title: str
    content: str
    author: str
    url: str
    score: int
    comments_count: int
    post_type: str
    subreddit: str
    created_at_platform: Optional[int] = None

    # Matching context
    matched_keyword: str
    matched_snippet: str
    intent_score: float
    matched_intent_phrase: str
    is_brand_mention: bool

    # Stage-3 status
    is_processed_to_lead: bool
    is_lead: bool

    # CRM status (denormalized for convenience — null if no Lead exists)
    lead_status: Optional[str] = None

    created_at: datetime


class MatchedPostListResponse(BaseModel):
    """Paginated feed response."""
    items: List[MatchedPostResponse]
    total: int
    page: int
    page_size: int


class MatchedPostFilter(BaseModel):
    """Query params for GET /posts?..."""
    project_id: Optional[UUID] = None
    source: Optional[str] = None
    search: Optional[str] = None
    subreddit: Optional[str] = None
    min_intent_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_brand_mention: Optional[bool] = None
    is_lead: Optional[bool] = None
    lead_status: Optional[str] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort_by: str = Field(default="intent_score")    # "intent_score" | "created_at" | "score"
    sort_desc: bool = True


class PostHighlightsResponse(BaseModel):
    """Returned by GET /posts/{id}/highlights — every keyword/snippet
    that contributed to this post being matched, so the frontend can
    render the post body with yellow highlights."""
    post_id: UUID
    highlights: List[dict]  # List[MatchHighlight], but kept as dict for flexibility
