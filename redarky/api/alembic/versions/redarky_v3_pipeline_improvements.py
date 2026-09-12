"""
Redarky v3 — pipeline improvements migration.

Adds:
  1. matched_posts.semantic_score + llm_score (stage transparency columns
     that also drive the new continuous scoring model)
  2. projects.platforms (per-product platform configuration, JSON)
  3. feedback_verifications + feedbacks tables (verified-email feedback)
  4. integrations table (slack / email / discord / teams / whatsapp / llm)

Optional (Postgres only, best-effort): enables the pgvector extension.
The embedding column itself stays a portable ARRAY(Float) — pgvector's
`<=>` operator is applied on the fly via a text cast (see
app/ai/embeddings.py), so no destructive column migration is required.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, do not edit
revision = "redarky_v3_pipeline"
down_revision = "redarky_v2_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. matched_posts: stage transparency columns ────────────────────────
    op.add_column("matched_posts", sa.Column("semantic_score", sa.Float(), nullable=True))
    op.add_column("matched_posts", sa.Column("llm_score", sa.Float(), nullable=True))

    # ── 2. projects: platform configuration ────────────────────────────────
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.add_column(
            "projects",
            sa.Column("platforms", postgresql.JSONB(), nullable=False,
                       server_default='["reddit"]'),
        )
        # Best-effort: enable pgvector when available (optional SQL-side
        # cosine fast path; falls back to Python cosine when not installed)
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass  # extension not installed — Python cosine fallback is used
    else:
        op.add_column(
            "projects",
            sa.Column("platforms", sa.JSON(), nullable=False,
                       server_default='["reddit"]'),
        )

    # ── 3. feedback tables ─────────────────────────────────────────────────
    op.create_table(
        "feedback_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_verifications_email", "feedback_verifications", ["email"])

    op.create_table(
        "feedbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedbacks_email", "feedbacks", ["email"])
    op.create_index("ix_feedbacks_status", "feedbacks", ["status"])
    op.create_index("ix_feedbacks_created_at", "feedbacks", ["created_at"])

    # ── 4. integrations table ──────────────────────────────────────────────
    if is_postgres:
        platforms_col = postgresql.JSONB()
    else:
        platforms_col = sa.JSON()
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("config", platforms_col, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integrations_user_id", "integrations", ["user_id"])
    op.create_index("ix_integrations_type", "integrations", ["type"])


def downgrade() -> None:
    op.drop_table("integrations")
    op.drop_table("feedbacks")
    op.drop_table("feedback_verifications")
    op.drop_column("projects", "platforms")
    op.drop_column("matched_posts", "llm_score")
    op.drop_column("matched_posts", "semantic_score")
