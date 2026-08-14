"""
app/sources/models.py

Targets domain — Monitored Sources (global registry of subreddits / accounts).

KEY CONCEPT: This is a GLOBAL registry, NOT per-user.
When user A adds r/SaaS and user B adds r/SaaS, only ONE row exists in
monitored_sources. Both projects link to it via project_sources.

This is what enables shared scraping: the Go scraper pulls r/SaaS once,
and the matching engine distributes the RawPosts to every project that
subscribed to it. Saves API quota dramatically.

No Apify coupling — source_type is generic ("reddit" | "x" | "linkedin").
Scheduling is handled by Celery Beat calling the Go scraper directly.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MonitoredSource(Base):
    """
    Global registry of subreddits / accounts / pages we scrape.

    `subscriber_count` is the number of projects watching this source.
    When it hits 0, `is_active` is flipped to False — Celery Beat will
    skip it when building the scraper payload, saving API calls.
    """
    __tablename__ = "monitored_sources"
    __table_args__ = (
        UniqueConstraint("source_type", "identifier", name="uq_sources_type_identifier"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # "reddit" | "x" | "linkedin" | ...
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # "r/SaaS" | "@elonmusk" | ...
    identifier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # How many projects are watching this? Drives the auto-pause logic.
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # How often (minutes) Celery Beat should include this in a batch.
    # Default 30 min — Reddit rate-limit friendly.
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectSource(Base):
    """
    Join table: which projects are watching which sources?

    When a user adds r/SaaS to their project:
      1. Look up (or create) the MonitoredSource row
      2. Create a ProjectSource link
      3. Increment monitored_sources.subscriber_count
      4. If subscriber_count went 0 → 1, flip is_active=True
    """
    __tablename__ = "project_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "monitored_source_id", name="uq_project_sources"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitored_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitored_sources.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
