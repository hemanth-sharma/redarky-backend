"""
app/sources/schemas.py
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    REDDIT = "reddit"
    X = "x"
    LINKEDIN = "linkedin"


# ── Add a source to a project (user input) ───────────────────────────────────
class SourceAddRequest(BaseModel):
    """User wants to monitor r/SaaS, @elonmusk, etc."""
    source_type: SourceType = SourceType.REDDIT
    identifier: str = Field(min_length=1, max_length=255)
    interval_minutes: int = Field(default=30, ge=5, le=1440)


# ── Response ──────────────────────────────────────────────────────────────────
class MonitoredSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: str
    identifier: str
    is_active: bool
    subscriber_count: int
    interval_minutes: int
    last_scraped_at: Optional[datetime] = None
    created_at: datetime


class ProjectSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    monitored_source_id: UUID
    source: MonitoredSourceResponse
    created_at: datetime


# ── Internal payload (used by scraper service to build Go payload) ───────────
class ScraperSourcePayload(BaseModel):
    """A flattened view used by the scraper service when building the
    Go scraper batch payload. Multiple projects watching the same
    subreddit collapse into one entry."""
    source_type: str
    identifier: str
