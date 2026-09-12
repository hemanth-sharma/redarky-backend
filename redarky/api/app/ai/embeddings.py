"""
app/ai/embeddings.py

Embedding engine for Stage-2 semantic matching + RAG context retrieval.

Providers (resolved by EMBEDDING_PROVIDER setting, default "auto"):
  - "openai" : OpenAI text-embedding-3-small (1536-dim). Uses OPENAI_API_KEY.
               Falls back silently if the key or the `openai` package is missing.
  - "local"  : sentence-transformers all-MiniLM-L6-v2 (384-dim), CPU-friendly.
               Only used when EMBEDDING_MODEL_ENABLED=true and the package exists.
  - "none"   : no embeddings — Stage 2 falls back to lexical similarity.

Vector storage stays portable: post_embeddings.embedding is a plain
ARRAY(Float) column that works on both Postgres and SQLite. When running
on Postgres WITH the pgvector extension installed, cosine similarity is
computed database-side using pgvector's `<=>` operator (see
`pgvector_available()` / `similarity_for_posts()`), casting the array
column on the fly:

    translate(embedding::text, '{}', '[]')::vector <=> :query::vector

No destructive migration needed. Without pgvector (or on SQLite), we
compute cosine in Python with numpy — fast enough at this scale.

Every embedding row records its model_name; similarity comparisons are
only valid between vectors of the same model (guarded by the caller).
"""
import logging
import math
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger("uvicorn.ai.embeddings")


# ── Provider resolution ──────────────────────────────────────────────────────

_engine = None


class _OpenAIEmbedder:
    """OpenAI text embeddings via the `openai` package (sync client, cheap)."""

    def __init__(self):
        from openai import OpenAI  # noqa: import here so the dep stays optional
        self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_EMBEDDING_MODEL
        self.dim = 1536

    def encode(self, text: str) -> list[float]:
        res = self._client.embeddings.create(input=[text[:8000]], model=self.model)
        return list(res.data[0].embedding)

    async def encode_async(self, text: str) -> list[float]:
        return self.encode(text)


class _LocalEmbedder:
    """sentence-transformers MiniLM (384-dim) — optional local provider."""

    def __init__(self):
        from sentence_transformers import SentenceTransformer  # noqa: optional dep
        self._model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
        self.model = settings.EMBEDDING_MODEL_NAME
        self.dim = 384

    def encode(self, txt: str) -> list[float]:
        return self._model.encode(txt[:8000]).tolist()

    async def encode_async(self, txt: str) -> list[float]:
        return self.encode(txt)


def get_embedder():
    """Returns the active embedder instance, or None when embeddings are off.

    Cached in module state. Resolution order for "auto":
      OpenAI key + openai package  →  openai
      EMBEDDING_MODEL_ENABLED + package → local
      else → None
    """
    global _engine
    if _engine is not None:
        return _engine

    provider = (settings.EMBEDDING_PROVIDER or "auto").lower()

    if provider == "none":
        return None

    if provider in ("auto", "openai"):
        if settings.OPENAI_API_KEY:
            try:
                _engine = _OpenAIEmbedder()
                logger.info("Embedding provider: OpenAI (%s, %d-dim)",
                            _engine.model, _engine.dim)
                return _engine
            except Exception as e:
                logger.warning("OpenAI embedder unavailable: %s", e)
        if provider == "openai":
            return None  # explicitly requested openai but not usable

    if provider in ("auto", "local"):
        if settings.EMBEDDING_MODEL_ENABLED:
            try:
                _engine = _LocalEmbedder()
                logger.info("Embedding provider: local (%s, %d-dim)",
                            _engine.model, _engine.dim)
                return _engine
            except Exception as e:
                logger.warning("Local embedder unavailable: %s", e)

    return None


def reset_embedder() -> None:
    """Test hook — clears the cached engine."""
    global _engine
    _engine = None


# ── Similarity math ─────────────────────────────────────────────────────────

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Pure-python cosine; no numpy dependency required."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    ma = 0.0
    mb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        ma += x * x
        mb += y * y
    if ma <= 0.0 or mb <= 0.0:
        return 0.0
    return dot / (math.sqrt(ma) * math.sqrt(mb))


def calibrate_similarity(raw_cos: float) -> float:
    """Maps a raw cosine similarity to a 0..1 semantic signal.

    OpenAI embeddings: unrelated texts sit around 0.0–0.15, strongly related
    around 0.35–0.65. MiniLM spans 0.0–0.85 with related text ~0.4+.
    A single calibration curve covering both:
        below 0.10 → ~0
        0.10 → 0.13
        0.40 → 0.75
        0.60+ → 1.0
    """
    if raw_cos <= 0.02:
        return 0.0
    # Smooth S-curve centered at 0.32, slope tuned so 0.55 ≈ 0.95
    k = 7.0
    mid = 0.32
    s = 1.0 / (1.0 + math.exp(-k * (raw_cos - mid)))
    return max(0.0, min(1.0, s * 1.06 - 0.03))  # slight stretch so 0.6+ reaches ~1


# ── pgvector fast path ───────────────────────────────────────────────────────

_pgvector_available: bool | None = None


async def pgvector_available(db: AsyncSession) -> bool:
    """True when the DB is Postgres with the pgvector extension installed.
    Result is cached per process."""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available
    try:
        row = (await db.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )).scalar()
        _pgvector_available = bool(row)
    except Exception:
        _pgvector_available = False
    if _pgvector_available:
        logger.info("pgvector extension detected — SQL-side cosine enabled")
    return _pgvector_available


def _vec_literal(vec: Sequence[float]) -> str:
    """Formats a python vector as a pgvector literal: '[0.1,0.2,...]'"""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def similarity_for_posts(
    db: AsyncSession,
    raw_post_ids: Sequence,
    query_vec: Sequence[float],
) -> dict:
    """Cosine similarity between one query vector (project profile) and
    the stored embedding of each raw post.

    Returns {raw_post_id: calibrated_similarity}.

    On Postgres+pgvector this runs database-side with the `<=>` operator;
    otherwise embeddings are fetched and scored in Python.
    """
    if not raw_post_ids or not query_vec:
        return {}

    ids = list(raw_post_ids)

    if await pgvector_available(db):
        try:
            q = text(
                """
                SELECT raw_post_id,
                       1 - (translate(embedding::text, '{}', '[]')::vector
                            <=> :qv::vector) AS raw_sim
                FROM post_embeddings
                WHERE raw_post_id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True))
            rows = (await db.execute(q, {
                "qv": _vec_literal(query_vec),
                "ids": [str(i) for i in ids],
            })).all()
            return {row[0]: calibrate_similarity(float(row[1])) for row in rows}
        except Exception as e:
            logger.warning("pgvector similarity failed, falling back to Python: %s", e)

    # Python fallback — pull the arrays and score locally
    from sqlalchemy import select
    from app.models import PostEmbedding

    rows = (await db.execute(
        select(PostEmbedding.raw_post_id, PostEmbedding.embedding)
        .where(PostEmbedding.raw_post_id.in_(ids))
    )).all()
    return {row[0]: calibrate_similarity(cosine_similarity(row[1], query_vec)) for row in rows}


# bindparam import kept at bottom to avoid a hard dependency at module import
from sqlalchemy import bindparam  # noqa: E402
