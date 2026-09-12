"""
app/posts/models.py

Delivery domain — MatchedPost (the user-facing feed).

A MatchedPost is a RawPost that:
  1. Passed Stage 1 (matched at least one project keyword)
  2. Got cloned into the project's space
  3. Got a Stage-2 intent_score (0.0–1.0)
  4. May or may not have been sent to the LLM (Stage 3)

This is what the frontend renders. TTL = project.data_retention_days.

`is_processed_to_lead` ensures the LLM never sees the same post twice.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, BigInteger, DateTime, ForeignKey, Index, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MatchedPost(Base):
    __tablename__ = "matched_posts"
    __table_args__ = (
        Index("ix_matched_posts_project_intent", "project_id", "intent_score"),
        Index("ix_matched_posts_expires_at", "expires_at"),
        # One MatchedPost per (project, raw_post) — prevents dups if matcher re-runs
        Index("uq_matched_posts_project_raw", "project_id", "raw_post_id", unique=True),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    raw_post_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_posts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # ── Denormalized content (so frontend list doesn't need a join) ──────────
    source: Mapped[str] = mapped_column(String(32), nullable=False)              # "reddit"
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="unknown")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    post_type: Mapped[str] = mapped_column(String(32), default="post", nullable=False)
    subreddit: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at_platform: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ── Matching Context (set by Stage 1 / Stage 2) ──────────────────────────
    # The single best keyword that triggered this match (for display)
    matched_keyword: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # The exact sentence/substring to highlight yellow on the frontend
    matched_snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Stage-2 score: 0.0 to 1.0. Higher = more relevant.
    intent_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Phrase that bumped the score (e.g. "alternative to", "looking for")
    matched_intent_phrase: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_brand_mention: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Stage-2 semantic similarity (0.0–1.0). NULL = not yet scored ─────────
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Stage-3 LLM confidence (0.0–1.0). NULL = not yet LLM-checked ─────────
    llm_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Stage-3 LLM tracking ─────────────────────────────────────────────────
    # Has this post been sent to the LLM yet? (prevents double-processing)
    is_processed_to_lead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # Did the LLM confirm high intent? (true → a Lead row was created)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── TTL ──────────────────────────────────────────────────────────────────
    # Set to created_at + project.data_retention_days
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
