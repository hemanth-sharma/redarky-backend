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
      with a provisional keyword-only score

STAGE 2 — Semantic Score
  For each new MatchedPost where semantic_score IS NULL:
    - Compute / fetch cached embedding of (title + " " + content)
    - Compute cosine similarity vs the project's profile embedding
      (built from goal_description + company info) — SQL-side via pgvector
      when available (see app/ai/embeddings.py), else OpenAI embeddings,
      else a continuous lexical-similarity fallback
    - Combine with intent-phrase signals (word-boundary regex, weighted by
      phrase strength) into a CONTINUOUS intent_score. No more quantized
      {0.3, 0.5, 0.7, 0.9} buckets.

STAGE 3 — LLM Filter (expensive, selective) — the Lead Agent
  For each MatchedPost where:
      intent_score >= project.llm_threshold
      AND is_processed_to_lead == False
    - Run the LangGraph lead agent (app/ai/lead_agent.py):
      retrieve_context → keyword_check → semantic_check → llm_grade → decide
    - If agent says lead:
        - Create Lead row with llm_reason + confidence
        - Set matched_posts.is_lead=True, is_processed_to_lead=True,
          llm_score=confidence, intent_score=blended final score
    - Else:
        - Set matched_posts.is_processed_to_lead=True, llm_score=confidence
    - Fan the new lead out to the owner's active integrations (Slack/Discord/
      Teams/WhatsApp/Email webhooks) — best-effort, never breaks the pipeline.

COST CONTROL:
  - Stage 1 runs on every raw post (cheap substring check)
  - Stage 2 runs only on Stage-1 survivors (~10-30% of raw)
  - Stage 3 runs only on Stage-2 high-scorers (~1-5% of raw); the agent's
    semantic router skips the LLM call entirely for weak posts
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select, and_, func, tuple_ as sa_tuple
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


# ═════════════════════════════════════════════════════════════════════════════
# SCORING MODEL (continuous — replaces the old 0.3/0.5/0.7/0.9 buckets)
# ═════════════════════════════════════════════════════════════════════════════
#
# intent_score = 0.05
#             + 0.32 * semantic_signal    (0..1 — embedding/lexical similarity)
#             + 0.38 * intent_signal      (0..1 — weighted buying phrases)
#             + 0.18 * keyword_signal    (0..1 — number of matched keywords)
#             + 0.07 * brand_signal       (0/1 — brand keyword mentioned)
#
# Range check (calibrated):
#   random post, matched 1 keyword, no phrases, weak semantic (~0.10) → ~0.13
#   related post, 1 keyword, one medium phrase, semantic 0.4          → ~0.55
#   hot post, 2+ keywords, strong phrase, brand mention, semantic 0.6  → ~0.95

# ── Intent phrases with strengths (word-boundary regex, NOT substring) ──────
# Strong phrases signal active buying intent; weak ones only mild interest.
INTENT_PHRASES: dict[str, float] = {
    # Active buying intent
    "alternative to": 1.00,
    "free alternative": 1.00,
    "open source alternative": 1.00,
    "looking for": 0.95,
    "any tool for": 0.95,
    "need a tool": 0.90,
    "need something": 0.80,
    "switch from": 1.00,
    "switching from": 1.00,
    "migrate from": 0.95,
    "cheaper than": 0.90,
    "recommend a": 0.85,
    "which tool": 0.85,
    "what do you use for": 0.85,
    "suggestions for": 0.75,
    "should i use": 0.80,
    # Medium intent
    "anyone using": 0.60,
    "better than": 0.60,
    "compared to": 0.55,
    "experience with": 0.55,
    "worth it": 0.50,
    # Weak / informational
    "vs": 0.30,
    "review of": 0.30,
    "honest review": 0.35,
}

# Precompiled word-boundary regexes (fixes "obvious" matching "vs",
# "recommended" matching "recommend", etc.)
_INTENT_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE), phrase, strength)
    for phrase, strength in INTENT_PHRASES.items()
]

# Kept for backwards compatibility with anything importing the old name
INTENT_PHRASES_LIST = list(INTENT_PHRASES.keys())

BASE_KEYWORD_SCORE = 0.3  # legacy constant — see matching_tasks.py
HIGH_SCORE_AUTO_PROMOTE = 0.85  # no-LLM auto-promotion floor


def intent_signal(text: str) -> tuple[float, str]:
    """Weighted intent-phrase signal.

    Returns (signal 0..1, best_phrase). Multiple distinct phrases stack
    with diminishing returns, so "looking for X vs Y" scores higher than
    each phrase alone but never exceeds 1.0.
    """
    if not text:
        return 0.0, ""
    hits: list[tuple[float, str]] = [
        (strength, phrase) for pattern, phrase, strength in _INTENT_PATTERNS
        if pattern.search(text)
    ]
    if not hits:
        return 0.0, ""
    hits.sort(reverse=True)
    best_strength, best_phrase = hits[0]
    signal = best_strength
    for strength, _ in hits[1:3]:  # up to 2 extra phrases stack
        signal += strength * 0.25
    return min(1.0, signal), best_phrase


def keyword_signal(matched_count: int) -> float:
    """1 keyword → 0.6, each additional +0.2, capped at 1.0."""
    if matched_count <= 0:
        return 0.0
    return min(1.0, 0.6 + 0.2 * (matched_count - 1))


def compute_intent_score(
    semantic: float,
    intent: float,
    keyword: float,
    brand: bool,
) -> float:
    """The continuous scoring formula documented above."""
    score = (
        0.05
        + 0.32 * max(0.0, min(1.0, semantic))
        + 0.38 * max(0.0, min(1.0, intent))
        + 0.18 * max(0.0, min(1.0, keyword))
        + (0.07 if brand else 0.0)
    )
    return round(min(1.0, max(0.0, score)), 4)


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
        .where(Project.is_pipeline_active == True)  # noqa: E712
    )).scalars().all()

    keywords_by_project: dict[uuid.UUID, list[Keyword]] = {}
    for kw in keywords:
        keywords_by_project.setdefault(kw.project_id, []).append(kw)

    project_ids = list(keywords_by_project.keys())
    projects = (await db.execute(
        select(Project).where(
            and_(
                Project.id.in_(project_ids),
                Project.is_pipeline_active == True,  # noqa: E712
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
            match_count = 0

            for kw in project_keywords:
                if kw.keyword_type not in ("include", "brand"):
                    continue
                kw_lower = kw.keyword.lower()
                if kw_lower in text:
                    match_count += 1
                    snippet = _extract_snippet(text, kw_lower, raw_post.title, raw_post.content)
                    if kw.keyword_type == "brand":
                        is_brand_mention = True
                    # Prefer brand matches for the displayed keyword
                    if best_match is None or kw.keyword_type == "brand":
                        best_match = (kw, snippet)

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

                # Provisional keyword-only score (continuous) — Stage 2 replaces it
                provisional = compute_intent_score(
                    semantic=0.0,
                    intent=intent_signal(text)[0],
                    keyword=keyword_signal(match_count),
                    brand=is_brand_mention,
                )

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
                    intent_score=provisional,
                    matched_intent_phrase="",
                    is_brand_mention=is_brand_mention,
                    semantic_score=None,   # ← marks "not yet Stage-2 scored"
                    llm_score=None,
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
    pos = text_lower.find(keyword_lower)
    if pos < 0:
        return keyword_lower

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
# STAGE 2: Semantic Score
# ═════════════════════════════════════════════════════════════════════════════

async def run_stage2_semantic_score(
    db: AsyncSession,
    limit: int = 500,
) -> SemanticScoreResult:
    """
    For each MatchedPost where semantic_score IS NULL (not yet scored):
      - Compute or fetch cached embedding for the underlying RawPost
      - Compute similarity vs the project profile embedding (per product —
        every product has its own profile: goal, company, keywords)
      - Combine with weighted intent-phrase signals into a continuous
        intent_score (see SCORING MODEL above)
    """
    # Load unscored matched posts — the NULL semantic_score is the marker
    matched_posts = (await db.execute(
        select(MatchedPost)
        .where(MatchedPost.semantic_score.is_(None))
        .order_by(MatchedPost.created_at.desc())
        .limit(limit)
    )).scalars().all()

    if not matched_posts:
        return SemanticScoreResult(
            matched_posts_scored=0,
            embeddings_computed=0,
            posts_above_threshold=0,
        )

    # ── Load projects (for profile text + thresholds) ────────────────────────
    project_ids = list({mp.project_id for mp in matched_posts})
    projects = (await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )).scalars().all()
    project_by_id = {p.id: p for p in projects}

    # ── Resolve the embedding provider ──────────────────────────────────────
    from app.ai.embeddings import get_embedder, similarity_for_posts
    from app.ai.lexical import lexical_similarity

    embedder = get_embedder()

    # ── Load existing embeddings (cache hit) ────────────────────────────────
    raw_post_ids = list({mp.raw_post_id for mp in matched_posts})
    existing_embs = (await db.execute(
        select(PostEmbedding).where(PostEmbedding.raw_post_id.in_(raw_post_ids))
    )).scalars().all()
    emb_by_raw = {e.raw_post_id: e for e in existing_embs}

    # ── Compute embeddings for raw posts that lack one ──────────────────────
    embeddings_computed = 0
    if embedder is not None:
        missing_ids = [pid for pid in raw_post_ids if pid not in emb_by_raw]
        if missing_ids:
            missing_raw = (await db.execute(
                select(RawPost).where(RawPost.id.in_(missing_ids))
            )).scalars().all()

            for raw in missing_raw:
                text = f"{raw.title} {raw.content}".strip() or raw.title
                try:
                    vec = embedder.encode(text)
                    emb = PostEmbedding(
                        raw_post_id=raw.id,
                        embedded_text=text[:2000],
                        embedding=vec,
                        model_name=embedder.model,
                    )
                    db.add(emb)
                    emb_by_raw[raw.id] = emb
                    embeddings_computed += 1
                except Exception as e:
                    logger.warning("Embedding failed for raw_post %s: %s", raw.id, e)
            if embeddings_computed:
                await db.commit()

    # ── Project profile embeddings (per product, cached per run) ────────────
    project_profile_emb: dict[uuid.UUID, list[float]] = {}
    if embedder is not None:
        for project in projects:
            profile_text = _profile_text(project)
            if profile_text:
                try:
                    project_profile_emb[project.id] = embedder.encode(profile_text)
                except Exception as e:
                    logger.warning("Profile embedding failed for project %s: %s", project.id, e)

    # ── SQL-side similarity via pgvector when available ──────────────────────
    sim_by_project: dict[uuid.UUID, dict[uuid.UUID, float]] = {}
    for pid, profile_vec in project_profile_emb.items():
        posts_for_project = [mp.raw_post_id for mp in matched_posts if mp.project_id == pid]
        try:
            sim_by_project[pid] = await similarity_for_posts(db, posts_for_project, profile_vec)
        except Exception as e:
            logger.warning("pgvector similarity failed for project %s: %s", pid, e)
            sim_by_project[pid] = {}

    # ── Score each matched post ─────────────────────────────────────────────
    scored = 0
    above_threshold = 0

    # Keyword-match counts per (raw_post, project) — feeds the keyword signal
    match_counts = await _match_counts(db, matched_posts)

    for mp in matched_posts:
        project = project_by_id.get(mp.project_id)
        if not project:
            continue

        text = f"{mp.title} {mp.content}".lower()

        # 1) Semantic signal — pgvector/OpenAI/lexical, always continuous
        semantic = sim_by_project.get(mp.project_id, {}).get(mp.raw_post_id)
        if semantic is None:
            if embedder is not None and mp.raw_post_id in emb_by_raw and project.id in project_profile_emb:
                from app.ai.embeddings import cosine_similarity, calibrate_similarity
                raw_sim = cosine_similarity(
                    emb_by_raw[mp.raw_post_id].embedding,
                    project_profile_emb[project.id],
                )
                semantic = calibrate_similarity(raw_sim)
            else:
                # Lexical fallback — continuous, deterministic, no deps
                semantic = lexical_similarity(
                    f"{mp.title} {mp.content}",
                    _profile_text(project),
                )

        # 2) Intent-phrase signal (weighted, word-boundary)
        intent, best_phrase = intent_signal(text)

        # 3) Keyword + brand signals
        kw_count = match_counts.get((mp.raw_post_id, mp.project_id), 1)
        brand = bool(mp.is_brand_mention)

        # 4) Final continuous score
        final_score = compute_intent_score(
            semantic=semantic,
            intent=intent,
            keyword=keyword_signal(kw_count),
            brand=brand,
        )

        mp.semantic_score = round(max(0.0, min(1.0, semantic)), 4)
        mp.intent_score = final_score
        mp.matched_intent_phrase = best_phrase

        if final_score >= (project.llm_threshold or 0.7):
            above_threshold += 1
        scored += 1

    await db.commit()

    return SemanticScoreResult(
        matched_posts_scored=scored,
        embeddings_computed=embeddings_computed,
        posts_above_threshold=above_threshold,
    )


def _profile_text(project: Project) -> str:
    """The product profile used for semantic matching — the user's goals,
    company info and site define what 'relevant' means. Per-product by design
    so each product's posts are scored against its own profile."""
    return " ".join(filter(None, [
        project.goal_description,
        project.company_name,
        project.company_description,
        project.company_url,
    ])).strip()


async def _profile_text_with_keywords(db: AsyncSession, project: Project) -> str:
    base = _profile_text(project)
    kws = (await db.execute(
        select(Keyword.keyword).where(
            and_(
                Keyword.project_id == project.id,
                Keyword.keyword_type.in_(["include", "brand"]),
            )
        )
    )).scalars().all()
    if kws:
        return f"{base} topics: {', '.join(kws[:40])}".strip()
    return base


async def _match_counts(
    db: AsyncSession, matched_posts: list[MatchedPost]
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    """KeywordMatch counts for each (raw_post, project) pair."""
    pairs = list({(mp.raw_post_id, mp.project_id) for mp in matched_posts})
    if not pairs:
        return {}
    # One query grouped by (raw_post, project)
    rows = (await db.execute(
        select(
            KeywordMatch.raw_post_id,
            KeywordMatch.project_id,
            func.count(KeywordMatch.id),
        ).where(
            sa_tuple(KeywordMatch.raw_post_id, KeywordMatch.project_id).in_(pairs)
        ).group_by(KeywordMatch.raw_post_id, KeywordMatch.project_id)
    )).all()
    return {(r[0], r[1]): int(r[2]) for r in rows}



# ═════════════════════════════════════════════════════════════════════════════
# STAGE 3: LLM Filter — the Lead Agent
# ═════════════════════════════════════════════════════════════════════════════

# Candidate floor for Stage 3 — posts below this never merit an LLM call
# (the agent's cost-control router enforces the per-project threshold on top)
STAGE3_CANDIDATE_FLOOR = 0.55


def _no_llm_auto_promote_threshold(embedder_active: bool) -> float:
    """Auto-promotion bar when no LLM is configured.

    With embeddings active, Stage-2 scores span the full 0..1 range, so the
    classic 0.85 bar stands. With the lexical fallback, scores are compressed
    (semantic rarely exceeds ~0.55), so the bar scales down to 0.65 — keeping
    the no-key dev experience functional without over-promoting noise.
    """
    return 0.85 if embedder_active else 0.65


async def run_stage3_llm_filter(
    db: AsyncSession,
    limit: int = 50,
    score_override: float | None = None,
) -> LLMFilterResult:
    """
    Runs the lead-grading agent (LangGraph when installed, sequential
    fallback otherwise) on unprocessed MatchedPosts that cleared Stage 2
    meaningfully (intent >= STAGE3_CANDIDATE_FLOOR).

    LLM path:      posts below the project's llm_threshold are skipped and
                   stay unprocessed (retried if the threshold is lowered or
                   a rescore raises their score — fixes the old behavior where
                   borderline posts were permanently skipped without ever
                   seeing an LLM).
    No-LLM path:   posts at/above the adaptive auto-promotion bar become
                   leads deterministically; weaker posts stay unprocessed so
                   they get agent-graded the moment an LLM is configured.
    """
    # Candidates: anything below the floor is never worth a re-look
    stmt = (
        select(MatchedPost, Project)
        .join(Project, MatchedPost.project_id == Project.id)
        .where(
            and_(
                MatchedPost.is_processed_to_lead == False,  # noqa: E712
                MatchedPost.intent_score >= STAGE3_CANDIDATE_FLOOR,
            )
        )
        .order_by(MatchedPost.intent_score.desc())
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    if not rows:
        return LLMFilterResult(
            matched_posts_processed=0,
            leads_created=0,
            llm_errors=0,
        )

    from app.ai.lead_agent import run_lead_agent
    from app.integrations import service as integrations_service

    # Whether any embedding provider is active — sets the no-LLM auto-promote bar
    try:
        from app.ai.embeddings import get_embedder
        embedder_active = get_embedder() is not None
    except Exception:
        embedder_active = False

    processed = 0
    leads_created = 0
    errors = 0

    # Cache: keywords per project + BYO-LLM config per owner (1 query each)
    keywords_by_project: dict[uuid.UUID, list[str]] = {}
    llm_config_cache: dict[uuid.UUID, dict | None] = {}

    for mp, project in rows:
        threshold = score_override if score_override is not None else (project.llm_threshold or 0.7)

        # ── Keywords for context (cached) ────────────────────────────────
        if project.id not in keywords_by_project:
            keywords_by_project[project.id] = (await db.execute(
                select(Keyword.keyword).where(
                    and_(
                        Keyword.project_id == project.id,
                        Keyword.keyword_type.in_(["include", "brand"]),
                    )
                )
            )).scalars().all()

        # ── BYO-LLM config for this project's owner (cached) ──────────────
        if project.owner_id not in llm_config_cache:
            try:
                llm_config_cache[project.owner_id] = await integrations_service.get_user_llm_config(
                    db, project.owner_id
                )
            except Exception as e:
                logger.warning("BYO-LLM lookup failed for user %s: %s", project.owner_id, e)
                llm_config_cache[project.owner_id] = None

        llm_config = llm_config_cache.get(project.owner_id)
        llm_available = bool((llm_config or {}).get("api_key") or settings.llm_enabled)

        try:
            if not llm_available:
                # ── No LLM configured — deterministic auto-promotion ─────────
                auto_promote = _no_llm_auto_promote_threshold(embedder_active)
                if mp.intent_score >= min(auto_promote, threshold):
                    lead = Lead(
                        project_id=project.id,
                        matched_post_id=mp.id,
                        status=LeadStatus.NEW.value,
                        intent_score=mp.intent_score,
                        url=mp.url,
                        matched_keyword=mp.matched_keyword,
                        llm_reason="High-score auto-promotion (no LLM configured — set OPENAI_API_KEY or connect a custom LLM in Integrations to enable agent grading)",
                    )
                    db.add(lead)
                    mp.is_lead = True
                    mp.llm_score = None
                    leads_created += 1
                    await _notify_lead(db, project, mp)
                    mp.is_processed_to_lead = True
                # Weaker posts stay unprocessed so the lead agent grades
                # them as soon as an LLM becomes available.
                processed += 1
                continue

            # ── Run the lead agent ────────────────────────────────────────
            if score_override is None and mp.intent_score < threshold:
                # Below the project's LLM threshold — leave unprocessed; the
                # agent will grade it if the threshold is lowered later.
                continue

            state = {
                "project_id": str(project.id),
                "project_name": project.name,
                "goal_description": project.goal_description or "",
                "goal_type": project.goal_type or "",
                "company_name": project.company_name or "",
                "company_description": project.company_description or "",
                "keywords": keywords_by_project.get(project.id, []),
                "llm_threshold": float(threshold),
                "post_id": str(mp.id),
                "post_title": mp.title or "",
                "post_content": mp.content or "",
                "post_author": mp.author or "",
                "post_subreddit": mp.subreddit or "",
                "matched_keyword": mp.matched_keyword or "",
                "matched_snippet": mp.matched_snippet or "",
                "semantic_score": mp.semantic_score,
            }
            result = await run_lead_agent(state, llm_config=llm_config)
            processed += 1

            is_lead = bool(result.get("is_lead"))
            confidence = float(result.get("confidence", 0.5))
            reason = str(result.get("reason", ""))[:1000]
            final_score = float(result.get("final_score", mp.intent_score))

            mp.llm_score = round(max(0.0, min(1.0, confidence)), 4)
            mp.is_processed_to_lead = True

            if is_lead:
                lead = Lead(
                    project_id=project.id,
                    matched_post_id=mp.id,
                    status=LeadStatus.NEW.value,
                    intent_score=round(final_score, 4),
                    url=mp.url,
                    matched_keyword=mp.matched_keyword,
                    llm_reason=reason or "Lead agent confirmed buying intent",
                )
                db.add(lead)
                mp.is_lead = True
                mp.intent_score = round(final_score, 4)  # agent-blended final score
                leads_created += 1
                await _notify_lead(db, project, mp)

        except Exception as e:
            logger.warning("Lead agent error on matched_post %s: %s", mp.id, e)
            errors += 1
            # Don't mark as processed — we'll retry next batch
            continue

    await db.commit()

    return LLMFilterResult(
        matched_posts_processed=processed,
        leads_created=leads_created,
        llm_errors=errors,
    )


async def _notify_lead(db: AsyncSession, project: Project, mp: MatchedPost) -> None:
    """Best-effort fan-out of a new lead to the owner's integrations.
    Never raises into the pipeline."""
    try:
        from app.integrations import service as integrations_service
        await integrations_service.notify_new_lead(
            db,
            owner_id=project.owner_id,
            product_name=project.name,
            lead_intent_score=mp.intent_score,
            matched_keyword=mp.matched_keyword,
            url=mp.url,
            title=mp.title,
            reason="",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Lead alert fan-out failed for project %s: %s", project.id, e)


# Legacy fallback entry point kept for API compatibility (unused now —
# no-LLM behavior is handled inline in run_stage3_llm_filter)
async def _stage3_no_llm_fallback(
    db: AsyncSession,
    candidates: list[MatchedPost],
    projects: dict,
) -> LLMFilterResult:
    """When no LLM is configured, auto-promote posts above 0.85 to Leads."""
    promoted = 0
    processed = 0

    for mp in candidates:
        project = projects.get(mp.project_id)
        if not project:
            continue
        threshold = project.llm_threshold or 0.7
        if mp.intent_score < threshold:
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
