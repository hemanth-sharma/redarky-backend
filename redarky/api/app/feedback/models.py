"""
app/feedback/models.py

Feedback domain — lets users (even pre-login visitors) send feedback,
but only after verifying ownership of their email address.

Flow:
  1. POST /feedback/request-verification  {email}
     → creates a FeedbackVerification row with a 6-digit code (hashed),
       valid for FEEDBACK_CODE_EXPIRE_MINUTES (default 15).
  2. POST /feedback/verify {email, code}
     → marks the row verified.
  3. POST /feedback {email, message}
     → only accepted when a verified, non-expired row exists for the email.

The verified email is the point: the product owner wants to be able to
reply to the person who left the feedback.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Text, DateTime, ForeignKey, func, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FeedbackVerification(Base):
    __tablename__ = "feedback_verifications"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # sha256 hash of the 6-digit code — never store the raw code
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedbacks"
    __table_args__ = (
        Index("ix_feedbacks_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # The verified email — this is the identity of the feedback author
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Optional link to a logged-in user (if the feedback came from an authed session)
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Free text
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional taxonomy: general | bug | feature_request | other
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)

    # Status for the owner's workflow: new | in_progress | closed
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
