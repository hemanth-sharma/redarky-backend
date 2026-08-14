"""
app/leads/schemas.py
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadStatusEnum(str, Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    CONTACTED = "contacted"
    WON = "won"
    LOST = "lost"
    IGNORED = "ignored"


# ── Create (usually called by the matching engine, not the user) ─────────────
class LeadCreate(BaseModel):
    project_id: UUID
    matched_post_id: UUID
    intent_score: float = Field(ge=0.0, le=1.0)
    url: str
    matched_keyword: str = ""
    llm_reason: str = ""


# ── Update ────────────────────────────────────────────────────────────────────
class LeadStatusUpdate(BaseModel):
    status: LeadStatusEnum


class LeadNotesUpdate(BaseModel):
    notes: Optional[str] = None


# ── Response ──────────────────────────────────────────────────────────────────
class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    matched_post_id: UUID
    status: str
    contacted_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    intent_score: float
    url: str
    matched_keyword: str
    llm_reason: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Denormalized post snapshot (so frontend list doesn't need a join)
    post_title: Optional[str] = None
    post_author: Optional[str] = None
    post_subreddit: Optional[str] = None


# ── Dashboard summary ─────────────────────────────────────────────────────────
class LeadStats(BaseModel):
    project_id: UUID
    total: int
    new: int
    reviewed: int
    contacted: int
    won: int
    lost: int
    ignored: int
