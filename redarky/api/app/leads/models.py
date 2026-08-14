"""
app/leads/models.py

CRM domain — high-intent leads.

A Lead is created ONLY when a MatchedPost:
  - Passes Stage 3 (LLM confirms high intent), OR
  - Has intent_score > 0.9 (configurable bypass for very high scores)

The Lead is the user's CRM view: they update status as they work it.
Updating status is the soft-gate against churn — if a user doesn't act
on leads, they pile up visibly on the dashboard.
"""
from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import String, Text, Float, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LeadStatus(str, Enum):
    NEW = "new"               # just created, user hasn't seen it yet
    REVIEWED = "reviewed"     # user opened it
    CONTACTED = "contacted"   # user reached out (manually logged)
    WON = "won"               # converted
    LOST = "lost"             # rejected
    IGNORED = "ignored"       # user dismissed without action


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    matched_post_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matched_posts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # ── Status workflow (the anti-churn soft gate) ───────────────────────────
    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.NEW.value, nullable=False, index=True)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Lead context (snapshot at creation time, in case post is deleted) ────
    intent_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    matched_keyword: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # Why the LLM flagged this as a lead (or "high-score bypass" if no LLM)
    llm_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # User's notes on this lead (filled in as they work it)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
