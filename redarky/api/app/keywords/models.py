"""
app/keywords/models.py

Targets domain — Keywords.

A Keyword belongs to exactly one Project. The matching engine uses:
  - "include" → post must contain this (or be semantically close)
  - "exclude" → post is filtered out if it contains this
  - "brand"   → post is flagged as a brand mention if it contains this
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (
        # Prevent duplicate (project_id, keyword) pairs
        UniqueConstraint("project_id", "keyword", name="uq_keywords_project_keyword"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Enum: "include" | "exclude" | "brand"
    keyword_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
