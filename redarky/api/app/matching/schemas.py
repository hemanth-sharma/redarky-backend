"""
app/matching/schemas.py

Schemas for the 3-stage matching pipeline.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Stage 1: keyword match ───────────────────────────────────────────────────
class KeywordMatchResult(BaseModel):
    """Result of running Stage 1 on a batch of raw posts."""
    raw_posts_processed: int
    keyword_matches_created: int
    matched_posts_created: int


# ── Stage 2: semantic scoring ────────────────────────────────────────────────
class SemanticScoreResult(BaseModel):
    """Result of running Stage 2 on a batch of matched posts."""
    matched_posts_scored: int
    embeddings_computed: int
    posts_above_threshold: int


# ── Stage 3: LLM filter ──────────────────────────────────────────────────────
class LLMFilterResult(BaseModel):
    """Result of running Stage 3 on a batch of high-score matched posts."""
    matched_posts_processed: int
    leads_created: int
    llm_errors: int


# ── Full pipeline (orchestrator result) ──────────────────────────────────────
class PipelineRunResult(BaseModel):
    """Returned after running all 3 stages end-to-end."""
    scraper_run_id: Optional[UUID] = None
    stage1: KeywordMatchResult
    stage2: SemanticScoreResult
    stage3: LLMFilterResult
    started_at: datetime
    finished_at: datetime


# ── MatchedPost response is in app/posts/schemas.py ─────────────────────────


# ── Highlight (used by frontend when user clicks a post) ────────────────────
class MatchHighlight(BaseModel):
    """Returned by GET /posts/{id}/highlights — the frontend uses this
    to highlight every matching keyword/snippet inside the post body."""
    matched_keyword: str
    matched_snippet: str
    intent_phrase: str = ""
    is_brand_mention: bool = False
