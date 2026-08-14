"""
Fresh initial migration for Redarky v2 data model.

This migration creates all 9 tables in the new architecture.
Run on a CLEAN database (drop existing redarky tables first if you
have stale ones from the previous schema).

Usage:
    alembic upgrade head

NOTE: Requires the `pgvector` PostgreSQL extension for the
      post_embeddings.embedding column (VECTOR(384)).

      Install it once per database:
        CREATE EXTENSION IF NOT EXISTS vector;
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, do not edit
revision = "redarky_v2_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: We use ARRAY(Float) for embeddings instead of pgvector's VECTOR type.
    # This avoids requiring the pgvector extension — cosine similarity is
    # computed in Python, which is fast enough at MVP scale (thousands of rows).
    # Switch to pgvector when you need indexed ANN search at million-row scale.

    # ── 1. users ─────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
        sa.Column("subscription_status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("stripe_customer_id", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"])

    # ── 3. projects ──────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("goal_description", sa.Text(), nullable=False),
        sa.Column("goal_type", sa.String(64), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=True),
        sa.Column("company_url", sa.String(512), nullable=True),
        sa.Column("company_description", sa.Text(), nullable=True),
        sa.Column("is_pipeline_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pipeline_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("llm_threshold", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_projects_goal_type", "projects", ["goal_type"])
    op.create_index("ix_projects_is_pipeline_active", "projects", ["is_pipeline_active"])

    # ── 4. keywords ──────────────────────────────────────────────────────────
    op.create_table(
        "keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword", sa.String(255), nullable=False),
        sa.Column("keyword_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "keyword", name="uq_keywords_project_keyword"),
    )
    op.create_index("ix_keywords_project_id", "keywords", ["project_id"])
    op.create_index("ix_keywords_keyword", "keywords", ["keyword"])
    op.create_index("ix_keywords_keyword_type", "keywords", ["keyword_type"])

    # ── 5. monitored_sources ─────────────────────────────────────────────────
    op.create_table(
        "monitored_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("subscriber_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_type", "identifier", name="uq_sources_type_identifier"),
    )
    op.create_index("ix_monitored_sources_source_type", "monitored_sources", ["source_type"])
    op.create_index("ix_monitored_sources_identifier", "monitored_sources", ["identifier"])
    op.create_index("ix_monitored_sources_is_active", "monitored_sources", ["is_active"])

    # ── 6. project_sources ───────────────────────────────────────────────────
    op.create_table(
        "project_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("monitored_source_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("monitored_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "monitored_source_id", name="uq_project_sources"),
    )
    op.create_index("ix_project_sources_project_id", "project_sources", ["project_id"])
    op.create_index("ix_project_sources_monitored_source_id", "project_sources", ["monitored_source_id"])

    # ── 7. scraper_runs ──────────────────────────────────────────────────────
    op.create_table(
        "scraper_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("total_items_pulled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_items_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_items_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_sent", postgresql.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scraper_runs_status", "scraper_runs", ["status"])

    # ── 8. raw_posts ─────────────────────────────────────────────────────────
    op.create_table(
        "raw_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scraper_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scraper_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("author", sa.String(255), nullable=False, server_default="unknown"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("post_type", sa.String(32), nullable=False, server_default="post"),
        sa.Column("subreddit", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at_platform", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_raw_posts_source_external", "raw_posts", ["source", "external_id"], unique=True)
    op.create_index("ix_raw_posts_source", "raw_posts", ["source"])
    op.create_index("ix_raw_posts_subreddit", "raw_posts", ["subreddit"])
    op.create_index("ix_raw_posts_scraper_run_id", "raw_posts", ["scraper_run_id"])
    op.create_index("ix_raw_posts_expires_at", "raw_posts", ["expires_at"])

    # ── 9. keyword_matches ───────────────────────────────────────────────────
    op.create_table(
        "keyword_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("raw_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("keywords.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("raw_post_id", "keyword_id", name="uq_keyword_matches_post_keyword"),
    )
    op.create_index("ix_keyword_matches_raw_post_id", "keyword_matches", ["raw_post_id"])
    op.create_index("ix_keyword_matches_keyword_id", "keyword_matches", ["keyword_id"])
    op.create_index("ix_keyword_matches_project_id", "keyword_matches", ["project_id"])

    # ── 10. post_embeddings (requires pgvector) ──────────────────────────────
    op.create_table(
        "post_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("raw_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedded_text", sa.Text(), nullable=False),
        # 384-dim vector for sentence-transformers MiniLM models
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=False,
                  server_default=sa.text("'{}'::double precision[]")),
        sa.Column("model_name", sa.String(64), nullable=False, server_default="minilm-l6-v2"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("raw_post_id", name="uq_post_embeddings_raw_post"),
    )
    op.create_index("ix_post_embeddings_raw_post_id", "post_embeddings", ["raw_post_id"], unique=True)

    # ── 11. matched_posts ────────────────────────────────────────────────────
    op.create_table(
        "matched_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("raw_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("author", sa.String(255), nullable=False, server_default="unknown"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("post_type", sa.String(32), nullable=False, server_default="post"),
        sa.Column("subreddit", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at_platform", sa.BigInteger(), nullable=True),
        sa.Column("matched_keyword", sa.String(255), nullable=False, server_default=""),
        sa.Column("matched_snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("intent_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("matched_intent_phrase", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_brand_mention", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_processed_to_lead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_lead", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_matched_posts_project_id", "matched_posts", ["project_id"])
    op.create_index("ix_matched_posts_raw_post_id", "matched_posts", ["raw_post_id"])
    op.create_index("ix_matched_posts_project_intent", "matched_posts", ["project_id", "intent_score"])
    op.create_index("ix_matched_posts_expires_at", "matched_posts", ["expires_at"])
    op.create_index("ix_matched_posts_is_processed_to_lead", "matched_posts", ["is_processed_to_lead"])
    op.create_index("uq_matched_posts_project_raw", "matched_posts",
                    ["project_id", "raw_post_id"], unique=True)

    # ── 12. leads ────────────────────────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_post_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("matched_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("intent_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("matched_keyword", sa.String(255), nullable=False, server_default=""),
        sa.Column("llm_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_leads_project_id", "leads", ["project_id"])
    op.create_index("ix_leads_matched_post_id", "leads", ["matched_post_id"])
    op.create_index("ix_leads_status", "leads", ["status"])


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_table("matched_posts")
    op.drop_table("post_embeddings")
    op.drop_table("keyword_matches")
    op.drop_table("raw_posts")
    op.drop_table("scraper_runs")
    op.drop_table("project_sources")
    op.drop_table("monitored_sources")
    op.drop_table("keywords")
    op.drop_table("projects")
    op.drop_table("users")
    # Don't drop the vector extension — other apps may use it.
