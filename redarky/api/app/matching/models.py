"""
app/matching/models.py

The 3-stage matching pipeline working data.

  Stage 1 → KeywordMatch   (raw_post × keyword hits)
  Stage 2 → PostEmbedding  (semantic vector + intent_score)
  Stage 3 → (no table; outputs go straight to Lead)

WHY these tables exist:

KeywordMatch:
  Records every (raw_post, keyword) pair that hit during Stage 1.
  This lets you answer "why was this post matched?" without re-running
  the matcher. The frontend highlights matched_keyword / matched_snippet
  using data joined from this table.

PostEmbedding:
  Stores the vector embedding of a RawPost's text (title + content).
  Used by Stage 2 to compute cosine similarity vs the project's
  profile embedding. Updated once per raw_post — not per project.
  Multiple projects share the same embedding; only the score differs.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KeywordMatch(Base):
    """
    Stage 1 output: every keyword hit on every raw_post.

    One RawPost can have many KeywordMatches (one per matching keyword).
    One Keyword can match many RawPosts.

    The matching engine creates MatchedPost rows by joining this table
    to project ownership — but KeywordMatch itself is project-scoped
    because keywords are project-scoped.
    """
    __tablename__ = "keyword_matches"
    __table_args__ = (
        UniqueConstraint("raw_post_id", "keyword_id", name="uq_keyword_matches_post_keyword"),
        Index("ix_keyword_matches_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    raw_post_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_posts.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    keyword_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("keywords.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # The exact substring that matched (for frontend highlighting)
    matched_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PostEmbedding(Base):
    """
    Stage 2 working data: vector embedding of a RawPost.

    Computed ONCE per RawPost (not per project). Multiple projects
    reuse the same embedding — only the per-project intent_score differs,
    and that score lives on matched_posts.

    Vector dimension: 384 (default for sentence-transformers/MiniLM).
    Adjust EMBEDDING_DIM in config if you swap models.

    Note: requires `pgvector` extension. Migration includes:
        CREATE EXTENSION IF NOT EXISTS vector;
    """
    __tablename__ = "post_embeddings"
    __table_args__ = (
        # One embedding per raw_post
        UniqueConstraint("raw_post_id", name="uq_post_embeddings_raw_post"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    raw_post_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_posts.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )

    # The text we embedded (for debugging / re-embedding if model changes)
    embedded_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 384-dim vector — adjust if you swap embedding models.
    # Uses ARRAY(Float) instead of pgvector's VECTOR type so we don't need
    # the pgvector extension installed. For MVP scale (thousands of posts)
    # cosine similarity in Python is fast enough. Switch to pgvector if/when
    # you need indexed ANN search at million-row scale.
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)

    # Which model produced this (e.g. "minilm-l6-v2")
    model_name: Mapped[str] = mapped_column(String(64), default="minilm-l6-v2", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
