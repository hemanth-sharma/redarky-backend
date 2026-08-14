"""
app/matching/service.py

The 3-stage matching pipeline.

This is the heart of Redarky. It turns raw scraped posts into user-facing
matched posts and high-intent leads.

STAGE 1 — Keyword Match (cheap, deterministic)
  For each new RawPost:
    - Fetch all active project keywords (include/brand)
    - Check if title/content contains any include keyword
    - Apply exclude keywords (filter out)
    - For each match: create KeywordMatch row + clone to MatchedPost
      with intent_score=0 (initial)

STAGE 2 — Semantic Score (non-LLM, local embeddings)
  For each new MatchedPost without a score:
    - Compute embedding of (title + " " + content) if not already cached
    - Compute cosine similarity vs the project's profile embedding
      (built from goal_description + company_description)
    - Boost score if matched_intent_phrase is present
      ("alternative to", "looking for", "any tool for", etc.)
    - Update matched_posts.intent_score, matched_snippet, matched_intent_phrase

STAGE 3 — LLM Filter (expensive, selective)
  For each MatchedPost where:
      intent_score >= project.llm_threshold
      AND is_processed_to_lead == False
    - Send to LLM with project context + post text
    - If LLM says "high intent lead":
        - Create Lead row with llm_reason
        - Set matched_posts.is_lead=True, is_processed_to_lead=True
    - Else:
        - Set matched_posts.is_processed_to_lead=True (don't re-process)

COST CONTROL:
  - Stage 1 runs on every raw post (cheap substring check)
  - Stage 2 runs only on Stage-1 survivors (~10-30% of raw)
  - Stage 3 runs only on Stage-2 high-scorers (~1-5% of raw)
  - LLM cost ≈ (raw_posts_per_day) × 0.05 × avg_tokens_per_call
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    RawPost, Keyword, KeywordMatch, PostEmbedding, MatchedPost,
    Project, Lead,
)
from app.leads.models import LeadStatus
from app.matching.schemas import (
    KeywordMatchResult, SemanticScoreResult, LLMFilterResult,
    PipelineRunResult,
)

logger = logging.getLogger("uvicorn.matching")


# ── Intent-phrase boosters (Stage-2 heuristic) ───────────────────────────────
# These phrases bump the intent_score when present in a matched post.
# They signal active buying intent — exactly what lead-gen users want.
INTENT_PHRASES = [
    "alternative to",
    "looking for",
    "any tool for",
    "anyone using",
    "switch from",
    "switching from",
    "migrate from",
    "better than",
    "cheaper than",
    "free alternative",
    "open source alternative",
    "recommend",
    "should i use",
    "vs",
    "compared to",
    "experience with",
    "review of",
    "honest review",
]

# Default intent score when Stage 1 keyword matches (before Stage 2 boost)
BASE_KEYWORD_SCORE = 0.3
INTENT_PHRASE_BOOST = 0.4
BRAND_MENTION_BOOST = 0.2


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1: Keyword Match
# ═════════════════════════════════════════════════════════════════════════════

async def run_stage1_keyword_match(
    db: AsyncSession,
    raw_post_ids: Sequence[uuid.UUID],
) -> KeywordMatchResult:
    """
    For each new RawPost, check against all active projects' keywords.
    Creates KeywordMatch + MatchedPost rows for every hit.
    """
    if not raw_post_ids:
        return KeywordMatchResult(
            raw_posts_processed=0,
            keyword_matches_created=0,
            matched_posts_created=0,
        )

    # Load raw posts (only those not yet expired)
    raw_posts = (await db.execute(
        select(RawPost).where(
            and_(
                RawPost.id.in_(raw_post_ids),
                RawPost.expires_at > datetime.now(timezone.utc),
            )
        )
    )).scalars().all()

    if not raw_posts:
        return KeywordMatchResult(
            raw_posts_processed=0,
            keyword_matches_created=0,
            matched_posts_created=0,
        )

    # Load all keywords for active projects (one query — cached in memory)
    keywords = (await db.execute(
        select(Keyword)
        .join(Project, Keyword.project_id == Project.id)
        .where(Project.is_pipeline_active == True)
    )).scalars().all()

    # Group keywords by project_id for efficient processing
    keywords_by_project: dict[uuid.UUID, list[Keyword]] = {}
    for kw in keywords:
        keywords_by_project.setdefault(kw.project_id, []).append(kw)

    # Load projects (to get data_retention_days for matched_post.expires_at)
    project_ids = list(keywords_by_project.keys())
    projects = (await db.execute(
        select(Project).where(
            and_(
                Project.id.in_(project_ids),
                Project.is_pipeline_active == True,
            )
        )
    )).scalars().all()
    project_by_id = {p.id: p for p in projects}

    total_matches = 0
    total_matched_posts = 0

    for raw_post in raw_posts:
        text = f"{raw_post.title} {raw_post.content}".lower()

        for project_id, project_keywords in keywords_by_project.items():
            project = project_by_id.get(project_id)
            if not project:
                continue

            # ── Check exclude keywords first — if any hit, skip this post ──
            excluded = any(
                kw.keyword.lower() in text
                for kw in project_keywords
                if kw.keyword_type == "exclude"
            )
            if excluded:
                continue

            # ── Check include + brand keywords ──────────────────────────────
            best_match = None  # (keyword, snippet)
            is_brand_mention = False

            for kw in project_keywords:
                if kw.keyword_type not in ("include", "brand"):
                    continue
                kw_lower = kw.keyword.lower()
                if kw_lower in text:
                    # Extract a snippet around the match
                    snippet = _extract_snippet(text, kw_lower, raw_post.title, raw_post.content)
                    if kw.keyword_type == "brand":
                        is_brand_mention = True
                    # Prefer brand matches for the displayed keyword
                    if best_match is None or kw.keyword_type == "brand":
                        best_match = (kw, snippet)

                    # Create KeywordMatch record
                    km = KeywordMatch(
                        raw_post_id=raw_post.id,
                        keyword_id=kw.id,
                        project_id=project_id,
                        matched_snippet=snippet,
                    )
                    db.add(km)
                    total_matches += 1

            # ── If any include/brand hit, clone to MatchedPost ─────────────
            if best_match:
                kw, snippet = best_match
                expires_at = datetime.now(timezone.utc) + timedelta(days=project.data_retention_days)

                mp = MatchedPost(
                    project_id=project_id,
                    raw_post_id=raw_post.id,
                    source=raw_post.source,
                    external_id=raw_post.external_id,
                    title=raw_post.title,
                    content=raw_post.content,
                    author=raw_post.author,
                    url=raw_post.url,
                    score=raw_post.score,
                    comments_count=raw_post.comments_count,
                    post_type=raw_post.post_type,
                    subreddit=raw_post.subreddit,
                    created_at_platform=raw_post.created_at_platform,
                    matched_keyword=kw.keyword,
                    matched_snippet=snippet,
                    intent_score=BASE_KEYWORD_SCORE,
                    matched_intent_phrase="",
                    is_brand_mention=is_brand_mention,
                    is_processed_to_lead=False,
                    is_lead=False,
                    expires_at=expires_at,
                )
                db.add(mp)
                total_matched_posts += 1

    await db.commit()

    return KeywordMatchResult(
        raw_posts_processed=len(raw_posts),
        keyword_matches_created=total_matches,
        matched_posts_created=total_matched_posts,
    )


def _extract_snippet(text_lower: str, keyword_lower: str, original_title: str, original_content: str) -> str:
    """Extracts a ~200-char snippet around the first occurrence of the keyword.
    Uses the original (non-lowered) text to preserve casing for display."""
    # Find the position in the lowercased text
    pos = text_lower.find(keyword_lower)
    if pos < 0:
        return keyword_lower

    # Map back to the original text — but original might be title+space+content
    original = f"{original_title} {original_content}"
    start = max(0, pos - 100)
    end = min(len(original), pos + len(keyword_lower) + 100)
    snippet = original[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(original):
        snippet = snippet + "..."
    return snippet


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2: Semantic Score (non-LLM)
# ═════════════════════════════════════════════════════════════════════════════

async def run_stage2_semantic_score(
    db: AsyncSession,
    limit: int = 500,
) -> SemanticScoreResult:
    """
    For each MatchedPost with intent_score == BASE_KEYWORD_SCORE
    (i.e., not yet scored by Stage 2):
      - Compute or fetch cached embedding for the underlying RawPost
      - Compute similarity vs project profile embedding
      - Apply intent-phrase boost
      - Update matched_post.intent_score, matched_intent_phrase
    """
    # Load unscored matched posts
    matched_posts = (await db.execute(
        select(MatchedPost)
        .where(MatchedPost.intent_score == BASE_KEYWORD_SCORE)
        .limit(limit)
    )).scalars().all()

    if not matched_posts:
        return SemanticScoreResult(
            matched_posts_scored=0,
            embeddings_computed=0,
            posts_above_threshold=0,
        )

    # ── Lazy-init the embedding model (only if we have posts to score) ─────
    embedder = _get_embedder()
    if embedder is None:
        # No embedding model configured — fall back to intent-phrase only
        return await _stage2_phrase_only_fallback(db, matched_posts)

    # ── Collect unique raw_post_ids that need embeddings ────────────────────
    raw_post_ids = list({mp.raw_post_id for mp in matched_posts})

    # ── Load existing embeddings (cache hit) ────────────────────────────────
    existing_embs = (await db.execute(
        select(PostEmbedding).where(PostEmbedding.raw_post_id.in_(raw_post_ids))
    )).scalars().all()
    emb_by_raw = {e.raw_post_id: e for e in existing_embs}

    # ── Load raw posts that need new embeddings ─────────────────────────────
    missing_ids = [pid for pid in raw_post_ids if pid not in emb_by_raw]
    if missing_ids:
        missing_raw = (await db.execute(
            select(RawPost).where(RawPost.id.in_(missing_ids))
        )).scalars().all()

        for raw in missing_raw:
            text = f"{raw.title} {raw.content}".strip() or raw.title
            try:
                vec = embedder.encode(text)  # list[float] of dim 384
                emb = PostEmbedding(
                    raw_post_id=raw.id,
                    embedded_text=text[:2000],  # truncate for storage
                    embedding=vec,
                    model_name=settings.EMBEDDING_MODEL_NAME,
                )
                db.add(emb)
                emb_by_raw[raw.id] = emb
            except Exception as e:
                logger.warning("Embedding failed for raw_post %s: %s", raw.id, e)
        await db.commit()

    # ── Load projects (for profile embeddings) ──────────────────────────────
    project_ids = list({mp.project_id for mp in matched_posts})
    projects = (await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )).scalars().all()
    project_by_id = {p.id: p for p in projects}

    # Cache project profile embeddings in-memory (per-run)
    project_profile_emb: dict[uuid.UUID, list[float]] = {}

    scored = 0
    above_threshold = 0
    embeddings_computed = len(missing_ids)

    for mp in matched_posts:
        post_emb = emb_by_raw.get(mp.raw_post_id)
        if not post_emb:
            continue  # embedding failed earlier

        project = project_by_id.get(mp.project_id)
        if not project:
            continue

        # Get or compute project profile embedding
        if mp.project_id not in project_profile_emb:
            profile_text = " ".join(filter(None, [
                project.goal_description,
                project.company_name,
                project.company_description,
            ])).strip()
            if profile_text:
                try:
                    project_profile_emb[mp.project_id] = embedder.encode(profile_text)
                except Exception:
                    project_profile_emb[mp.project_id] = []
            else:
                project_profile_emb[mp.project_id] = []

        # ── Compute cosine similarity ──────────────────────────────────────
        profile_vec = project_profile_emb.get(mp.project_id, [])
        similarity = 0.0
        if profile_vec:
            similarity = _cosine_sim(post_emb.embedding, profile_vec)

        # ── Apply intent-phrase boost ──────────────────────────────────────
        text = f"{mp.title} {mp.content}".lower()
        intent_phrase = ""
        phrase_boost = 0.0
        for phrase in INTENT_PHRASES:
            if phrase in text:
                intent_phrase = phrase
                phrase_boost = INTENT_PHRASE_BOOST
                break

        # ── Apply brand mention boost ──────────────────────────────────────
        brand_boost = BRAND_MENTION_BOOST if mp.is_brand_mention else 0.0

        # ── Final score (clamped 0.0–1.0) ──────────────────────────────────
        # Weighting: 50% semantic similarity, 50% heuristics
        final_score = min(1.0, (similarity * 0.5) + (BASE_KEYWORD_SCORE + phrase_boost + brand_boost) * 0.5)

        mp.intent_score = round(final_score, 4)
        mp.matched_intent_phrase = intent_phrase

        if final_score >= (project.llm_threshold or 0.7):
            above_threshold += 1
        scored += 1

    await db.commit()

    return SemanticScoreResult(
        matched_posts_scored=scored,
        embeddings_computed=embeddings_computed,
        posts_above_threshold=above_threshold,
    )


async def _stage2_phrase_only_fallback(db: AsyncSession, matched_posts: list[MatchedPost]) -> SemanticScoreResult:
    """Used when no embedding model is configured. Scores based purely
    on intent phrases and brand mentions — cruder but functional.
    Lets the MVP run without sentence-transformers installed."""
    scored = 0
    above_threshold = 0

    # Group by project to get thresholds
    project_ids = list({mp.project_id for mp in matched_posts})
    projects = (await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )).scalars().all()
    project_by_id = {p.id: p for p in projects}

    for mp in matched_posts:
        project = project_by_id.get(mp.project_id)
        if not project:
            continue

        text = f"{mp.title} {mp.content}".lower()
        intent_phrase = ""
        phrase_boost = 0.0
        for phrase in INTENT_PHRASES:
            if phrase in text:
                intent_phrase = phrase
                phrase_boost = INTENT_PHRASE_BOOST
                break

        brand_boost = BRAND_MENTION_BOOST if mp.is_brand_mention else 0.0
        final_score = min(1.0, BASE_KEYWORD_SCORE + phrase_boost + brand_boost)

        mp.intent_score = round(final_score, 4)
        mp.matched_intent_phrase = intent_phrase

        if final_score >= (project.llm_threshold or 0.7):
            above_threshold += 1
        scored += 1

    await db.commit()

    return SemanticScoreResult(
        matched_posts_scored=scored,
        embeddings_computed=0,
        posts_above_threshold=above_threshold,
    )


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# Lazy-loaded embedder — only imported if Stage 2 actually runs
_embedder = None
def _get_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    if not settings.EMBEDDING_MODEL_ENABLED:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
        logger.info("Loaded embedding model: %s", settings.EMBEDDING_MODEL_NAME)
        return _embedder
    except ImportError:
        logger.warning(
            "sentence-transformers not installed — Stage 2 falls back to phrase-only scoring. "
            "Install with: pip install sentence-transformers"
        )
        return None
    except Exception as e:
        logger.warning("Failed to load embedding model: %s", e)
        return None


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3: LLM Filter
# ═════════════════════════════════════════════════════════════════════════════

async def run_stage3_llm_filter(
    db: AsyncSession,
    limit: int = 50,
    score_override: float | None = None,
) -> LLMFilterResult:
    """
    For each MatchedPost where:
        intent_score >= project.llm_threshold
        AND is_processed_to_lead == False
      Send to LLM with project context.
      If LLM confirms high intent → create Lead.
      Mark is_processed_to_lead=True either way.
    """
    # Load candidates
    stmt = (
        select(MatchedPost)
        .where(MatchedPost.is_processed_to_lead == False)
        .order_by(MatchedPost.intent_score.desc())
        .limit(limit)
    )
    candidates = (await db.execute(stmt)).scalars().all()

    if not candidates:
        return LLMFilterResult(
            matched_posts_processed=0,
            leads_created=0,
            llm_errors=0,
        )

    # Load projects
    project_ids = list({mp.project_id for mp in candidates})
    projects = (await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )).scalars().all()
    project_by_id = {p.id: p for p in projects}

    # ── Lazy LLM client ─────────────────────────────────────────────────────
    llm_client = _get_llm_client()
    if llm_client is None:
        # No LLM configured — auto-promote high-score posts to Leads
        return await _stage3_no_llm_fallback(db, candidates, project_by_id)

    processed = 0
    leads_created = 0
    errors = 0

    for mp in candidates:
        project = project_by_id.get(mp.project_id)
        if not project:
            continue

        # Skip if below threshold (unless override is set)
        threshold = score_override if score_override is not None else (project.llm_threshold or 0.7)
        if mp.intent_score < threshold:
            mp.is_processed_to_lead = True  # mark so we don't re-check
            continue

        # ── Call LLM ────────────────────────────────────────────────────────
        try:
            is_lead, reason = await llm_client.classify_lead(
                project=project,
                post=mp,
            )
            processed += 1

            if is_lead:
                lead = Lead(
                    project_id=project.id,
                    matched_post_id=mp.id,
                    status=LeadStatus.NEW.value,
                    intent_score=mp.intent_score,
                    url=mp.url,
                    matched_keyword=mp.matched_keyword,
                    llm_reason=reason,
                )
                db.add(lead)
                mp.is_lead = True
                mp.is_processed_to_lead = True
                leads_created += 1
            else:
                mp.is_processed_to_lead = True

        except Exception as e:
            logger.warning("LLM error on matched_post %s: %s", mp.id, e)
            errors += 1
            # Don't mark as processed — we'll retry next batch
            continue

    await db.commit()

    return LLMFilterResult(
        matched_posts_processed=processed,
        leads_created=leads_created,
        llm_errors=errors,
    )


async def _stage3_no_llm_fallback(
    db: AsyncSession,
    candidates: list[MatchedPost],
    projects: dict,
) -> LLMFilterResult:
    """When no LLM is configured, auto-promote posts above 0.85 to Leads
    with reason='high-score bypass (no LLM)'. Lets the MVP run end-to-end
    without an OpenAI key."""
    promoted = 0
    processed = 0
    HIGH_SCORE_AUTO_PROMOTE = 0.85

    for mp in candidates:
        project = projects.get(mp.project_id)
        if not project:
            continue
        threshold = project.llm_threshold or 0.7
        if mp.intent_score < threshold:
            mp.is_processed_to_lead = True
            continue
        processed += 1
        if mp.intent_score >= HIGH_SCORE_AUTO_PROMOTE:
            lead = Lead(
                project_id=project.id,
                matched_post_id=mp.id,
                status=LeadStatus.NEW.value,
                intent_score=mp.intent_score,
                url=mp.url,
                matched_keyword=mp.matched_keyword,
                llm_reason="High-score auto-promotion (no LLM configured)",
            )
            db.add(lead)
            mp.is_lead = True
            promoted += 1
        mp.is_processed_to_lead = True

    await db.commit()
    return LLMFilterResult(
        matched_posts_processed=processed,
        leads_created=promoted,
        llm_errors=0,
    )


# Lazy LLM client
_llm_client = None
def _get_llm_client():
    global _llm_client
    if _llm_client is not None:
        return _llm_client
    if not settings.LLM_ENABLED:
        return None
    try:
        from app.matching.llm_client import LLMLeadClassifier
        _llm_client = LLMLeadClassifier()
        return _llm_client
    except Exception as e:
        logger.warning("Failed to init LLM client: %s", e)
        return None


# ═════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

async def run_pipeline(
    db: AsyncSession,
    raw_post_ids: Sequence[uuid.UUID],
) -> PipelineRunResult:
    """
    Runs all 3 stages end-to-end on a batch of newly-ingested raw posts.
    Called by the Celery worker after ingestion completes.
    """
    started = datetime.now(timezone.utc)

    stage1 = await run_stage1_keyword_match(db, raw_post_ids)
    stage2 = await run_stage2_semantic_score(db)
    stage3 = await run_stage3_llm_filter(db)

    finished = datetime.now(timezone.utc)

    return PipelineRunResult(
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        started_at=started,
        finished_at=finished,
    )
