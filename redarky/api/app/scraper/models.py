"""
app/scraper/models.py

Scraper observability — every Go scraper batch is logged here.

WHY: When a user complains "I'm not getting leads", you need to debug:
  1. Did Celery Beat actually fire the batch?      → scraper_runs row exists?
  2. Did the Go scraper pull any items?            → total_items_pulled > 0?
  3. Did the items land in raw_posts?              → count(raw_posts.scraper_run_id)
  4. Did the matching engine process them?         → count(matched_posts.raw_post_id)
  5. Did any pass Stage 2 / Stage 3?               → check matched_posts.intent_score

Without scraper_runs, step 1 and 2 are blind spots.

`payload_sent` is a JSON snapshot of exactly what we told Go to scrape,
so you can reproduce a batch manually if needed.
"""
from datetime import datetime
from uuid import uuid4
from typing import Any

from sqlalchemy import String, Integer, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScraperRun(Base):
    __tablename__ = "scraper_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # "running" | "success" | "failed" | "partial"
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False, index=True)

    # How many items the Go scraper returned
    total_items_pulled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # How many were NEW (not already in raw_posts from a previous run)
    new_items_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # How many were duplicates (skipped)
    duplicate_items_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Snapshot of the payload we sent to Go:
    #   {
    #     "keywords": ["notion alternative", "competitor to linear", ...],
    #     "subreddits": ["r/SaaS", "r/productivity", ...],
    #     "sort": "new",
    #     "since_timestamp": 1697000000
    #   }
    payload_sent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Error message if status == "failed"
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
