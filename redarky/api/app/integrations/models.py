"""
app/integrations/models.py

Integration domain — external channels a user can connect:
  slack | email | discord | teams | whatsapp | llm

- slack/discord/teams/whatsapp: webhook-style push notifications for new leads.
- email: summary/alert destination.
- llm: bring-your-own LLM (OpenAI-compatible base URL + API key + model).
  When active, Stage 3 uses the user's own LLM instead of the platform default.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # slack | email | discord | teams | whatsapp | llm
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Type-specific config:
    #   slack/discord/teams/whatsapp → {"webhook_url": "..."}
    #   email                       → {"to_email": "..."}
    #   llm                         → {"api_key": "...", "base_url": "...", "model": "..."}
    config: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
