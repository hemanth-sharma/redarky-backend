"""
app/projects/schemas.py
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Enums (mirrors DB string columns; kept as Python enums for validation) ───
from enum import Enum


class GoalType(str, Enum):
    LEAD_GEN = "lead_gen"
    BRAND_MONITORING = "brand_monitoring"
    COMPETITOR_TRACKING = "competitor_tracking"


# ── Create ────────────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal_description: str = Field(min_length=1)
    goal_type: GoalType

    # Optional company context — strengthens Stage-2 semantic matching
    company_name: Optional[str] = None
    company_url: Optional[str] = None
    company_description: Optional[str] = None

    # Default retention: 30 days. Frontend lets user pick 5 / 10 / 30.
    data_retention_days: int = Field(default=30, ge=1, le=90)

    # Stage-2 LLM threshold (0.0–1.0). Default 0.7.
    llm_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


# ── Update ────────────────────────────────────────────────────────────────────
class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    goal_description: Optional[str] = None
    goal_type: Optional[GoalType] = None
    company_name: Optional[str] = None
    company_url: Optional[str] = None
    company_description: Optional[str] = None
    data_retention_days: Optional[int] = Field(default=None, ge=1, le=90)
    llm_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ── Pipeline activation (frontend button) ────────────────────────────────────
class PipelineActivate(BaseModel):
    activate: bool


# ── Response ──────────────────────────────────────────────────────────────────
class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    goal_description: str
    goal_type: str
    company_name: Optional[str] = None
    company_url: Optional[str] = None
    company_description: Optional[str] = None
    is_pipeline_active: bool
    pipeline_activated_at: Optional[datetime] = None
    data_retention_days: int
    llm_threshold: float
    created_at: datetime
    updated_at: datetime


# ── Stats (for dashboard) ─────────────────────────────────────────────────────
class ProjectStats(BaseModel):
    project_id: UUID
    matched_posts_count: int
    leads_count: int
    unactioned_leads_count: int
    active_keywords_count: int
    active_sources_count: int
    last_scraper_run_at: Optional[datetime] = None
