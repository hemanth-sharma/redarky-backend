"""
app/projects/models.py

Project domain — the container for everything a user is trying to achieve.

A Project holds:
  - The goal (lead_gen | brand_monitoring | competitor_tracking)
  - The company context (optional — used for semantic matching)
  - The pipeline state (is the user actively listening?)
  - The data retention policy (5 / 10 / 30 days — user-controlled)

This is the single source of truth for "what is this user trying to do".
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Text, Boolean, Integer, DateTime, ForeignKey, func, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Identity ─────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Plain-text mission: "Find people looking for Notion alternatives"
    goal_description: Mapped[str] = mapped_column(Text, nullable=False)
    # Enum: "lead_gen" | "brand_monitoring" | "competitor_tracking"
    goal_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # ── Company Context (optional, used for Stage-2 semantic matching) ───────
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Free-text company description — gives the semantic matcher more signal
    company_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Pipeline State (toggled by frontend button) ──────────────────────────
    is_pipeline_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    pipeline_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Data Retention (user-controlled; 5 / 10 / 30 days) ──────────────────
    # Applied to MatchedPost rows. RawPosts always expire in 7 days regardless.
    data_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # ── Stage-2 Semantic Matching Config ─────────────────────────────────────
    # Threshold above which a MatchedPost is sent to the LLM (Stage 3).
    # Default 0.7 — tune as you gather data.
    llm_threshold: Mapped[float] = mapped_column(default=0.7, nullable=False)

    # Which data platforms this product's pipeline pulls from.
    # Supported today: "reddit". Others (hacker_news, x, linkedin, ...) can be
    # enabled per product as their collectors come online — the scraper
    # payload builder respects this list when aggregating the shared batch.
    platforms: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=lambda: ["reddit"], nullable=False, server_default='["reddit"]',
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
