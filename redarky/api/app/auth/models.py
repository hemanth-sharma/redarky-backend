"""
app/auth/models.py

Identity & Billing domain.

For MVP we keep subscription info directly on the User table.
When you introduce Stripe later, you can either:
  (a) keep these columns and just add stripe_customer_id, OR
  (b) split into a separate Subscriptions table — minimal migration.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Subscription (MVP-flat, no separate table yet) ──────────────────────
    # plan: "free" | "pro" | "agency"  (only "free" exists for MVP)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    # subscription_status: "active" | "canceled" | "trialing"
    subscription_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # Future-proof: drop in stripe_customer_id when billing lands
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
