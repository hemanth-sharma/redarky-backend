"""
app/ingestion/models.py

Global ingestion domain — the data lake.

RawPost is a GLOBAL dedup table. It is NOT scoped to a project.
The matching engine decides which projects each RawPost belongs to.

TTL: 7 days (hardcoded). The cleanup cron deletes rows past expires_at.
This keeps the table small — we only keep raw data long enough for the
matching engine to process it.

Dedup key: (source, external_id) — e.g. ("reddit", "t3_abc123").
If the Go scraper returns the same Reddit post in two batches, the second
insert is silently ignored (ON CONFLICT DO NOTHING).
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    String, Text, Integer, BigInteger, DateTime, ForeignKey, Index, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RawPost(Base):
    __tablename__ = "raw_posts"
    __table_args__ = (
        # Dedup index — used by ON CONFLICT DO NOTHING
        Index("uq_raw_posts_source_external", "source", "external_id", unique=True),
        # TTL cleanup index — cron deletes WHERE expires_at < now()
        Index("ix_raw_posts_expires_at", "expires_at"),
        Index("ix_raw_posts_subreddit", "subreddit"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Which scraper batch pulled this? (FK to scraper_runs.id)
    scraper_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scraper_runs.id", ondelete="SET NULL"),
        nullable=True, index=True
    )

    # ── Provenance ───────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # "reddit"
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)        # "t3_abc123"

    # ── Content ──────────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="unknown")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # "post" | "comment"
    post_type: Mapped[str] = mapped_column(String(32), default="post", nullable=False)
    subreddit: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # ── Platform timestamp (Unix epoch, seconds) ─────────────────────────────
    created_at_platform: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ── TTL ──────────────────────────────────────────────────────────────────
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
