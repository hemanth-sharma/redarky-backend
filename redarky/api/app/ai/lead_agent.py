"""
app/ai/lead_agent.py

The Stage-3 lead-grading agent.

Built with LangGraph (StateGraph) + LangChain when those packages are
installed, expressing Redarky's 3-stage filter as an honest agent
workflow:

    ┌─────────────┐    ┌──────────────────┐    ┌────────────────┐
    │ retrieve_   │ →  │ keyword_check    │ →  │ semantic_check │
    │ context(RAG)│    │ (verify Stage 1)│    │ (verify Stage2)│
    └─────────────┘    └──────────────────┘    └───────┬────────┘
                                                   │ score < threshold
                                                   ▼  → reject (skip LLM, cost control)
                                              ┌──────────┐
                                              │ llm_grade│ → decide → END
                                              └──────────┘

Nodes:
  retrieve_context  RAG-style: builds a focused project brief (goal,
                    company, keywords) relevant to the post by embedding
                    similarity between the post and the project's context
                    chunks.
  keyword_check     Confirms Stage-1 keyword match still holds (defensive).
  semantic_check    Re-reads the Stage-2 semantic score; decides whether
                    the post merits the (expensive) LLM call.
  llm_grade         Calls the LLM with a structured-output prompt.
  decide            Blends stage scores + LLM confidence into the final
                    verdict {is_lead, confidence, reason}.

DEGRADATION (important — the pipeline must never hard-require langchain):
  If langgraph/langchain are not installed, `run_lead_agent` transparently
  executes the same steps as plain sequential Python with the raw httpx
  LLM client. Same inputs, same outputs, no framework needed.

The BYO-LLM path is honored: when the project owner has an active "llm"
integration, its api_key/base_url/model override the platform defaults.
"""
import logging
from typing import Any, Dict, Optional, TypedDict

from app.config import settings

logger = logging.getLogger("uvicorn.ai.lead_agent")


# ── Shared state ──────────────────────────────────────────────────────────────

class LeadAgentState(TypedDict, total=False):
    # Inputs
    project_id: str
    project_name: str
    goal_description: str
    goal_type: str
    company_name: str
    company_description: str
    keywords: list[str]          # include + brand keywords for this project
    llm_threshold: float
    post_id: str
    post_title: str
    post_content: str
    post_author: str
    post_subreddit: str
    matched_keyword: str
    matched_snippet: str
    semantic_score: Optional[float]   # stage-2 output (0..1)
    # Intermediates
    project_brief: str           # retrieved context for the LLM
    keyword_ok: bool
    # Outputs
    is_lead: bool
    confidence: float            # 0..1
    reason: str
    skipped_llm: bool            # True when we short-circuited before the LLM


# ── LLM call (framework-free, works with BYO config) ───────────────────────

async def call_llm_grade(
    state: LeadAgentState,
    llm_config: Optional[Dict[str, Any]] = None,
) -> LeadAgentState:
    """Structured-output LLM grading. Uses LangChain when available, else
    falls back to a raw httpx call against the OpenAI-compatible endpoint."""
    api_key = (llm_config or {}).get("api_key") or settings.llm_api_key
    base_url = (llm_config or {}).get("base_url") or settings.LLM_API_URL
    model = (llm_config or {}).get("model") or settings.LLM_MODEL_NAME

    if not api_key:
        # No LLM anywhere — caller handles this before reaching here, but keep safe
        return {**state, "is_lead": False, "confidence": 0.0,
                "reason": "No LLM configured", "skipped_llm": True}

    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(state)

    # ── Preferred path: LangChain ──────────────────────────────────────────
    try:
        import asyncio
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        global _GradeSchema
        if _GradeSchema is None:
            _GradeSchema = _get_grade_schema()

        llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=0.2,
            max_tokens=250,
            # base_url accepts the /v1 root (langchain appends the path)
            base_url=_langchain_base_url(base_url),
        )
        chain = llm.with_structured_output(_GradeSchema)
        result = await asyncio.wait_for(
            chain.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]),
            timeout=45.0,
        )
        return {
            **state,
            "is_lead": bool(result.is_lead),
            "confidence": float(result.confidence),
            "reason": str(result.reason or "")[:1000],
            "skipped_llm": False,
        }
    except ImportError:
        pass  # langchain not installed — fall through to raw httpx
    except Exception as e:
        logger.warning("LangChain grade failed (%s) — trying raw httpx path", e)

    # ── Fallback path: raw httpx ────────────────────────────────────────────
    import json
    import httpx

    async with httpx.AsyncClient(timeout=45.0) as client:
        res = await client.post(
            base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 250,
                "response_format": {"type": "json_object"},
            },
        )
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return {
            **state,
            "is_lead": bool(parsed.get("is_lead", False)),
            "confidence": _clamp01(parsed.get("confidence", 0.5)),
            "reason": str(parsed.get("reason", ""))[:1000],
            "skipped_llm": False,
        }


def _langchain_base_url(url: str) -> str:
    """LangChain wants the API root (e.g. https://api.openai.com/v1),
    not the full /chat/completions path."""
    if url.rstrip("/").endswith("/chat/completions"):
        return url.rstrip("/")[: -len("/chat/completions")]
    return url


def _clamp01(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


# ── Pydantic schema for structured output ───────────────────────────────────

def _get_grade_schema():
    from pydantic import BaseModel, Field

    class GradeSchema(BaseModel):
        """Structured output returned by the grader LLM."""
        is_lead: bool = Field(description="True only if the post shows active, actionable buying intent for the project")
        confidence: float = Field(ge=0.0, le=1.0, description="How confident you are in this verdict (0.0–1.0)")
        reason: str = Field(description="One or two sentences explaining the verdict")

    return GradeSchema


# Lazily-built so a missing langchain never breaks import time
_GradeSchema = None


# ── Prompts ──────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    return """You are the lead-grading agent inside Redarky, a social-listening lead-generation platform.

You will receive a PROJECT BRIEF (what the user's product is and what kind of
leads they want) and a POST from social media that already passed two automated
filters (keyword match + semantic similarity to the project).

Your job: decide FINAL whether this post is a genuine, actionable lead.

Return JSON with exactly these fields:
  {"is_lead": true|false, "confidence": 0.0-1.0, "reason": "short explanation"}

is_lead = TRUE only when the post shows real, actionable buying intent that
matches the project's offering — e.g. actively looking for a solution like the
user's product, asking for alternatives to the user's competitors, describing a
pain the user's product directly solves, or asking the community which tool to
pick in the user's category.

is_lead = FALSE for: general discussion, venting, job posts, self-promotion,
link drops, questions about unrelated topics, complaints about the user's own
product, or purely informational/research posts with no intent to buy or switch.

Be precise: a wrong lead costs the user's trust more than a missed weak lead."""


def _build_user_prompt(state: LeadAgentState) -> str:
    sem = state.get("semantic_score")
    sem_display = f"{sem:.2f}" if isinstance(sem, (int, float)) else "n/a"
    return f"""PROJECT BRIEF:
  Product / project name: {state.get('project_name', '(unnamed)')}
  Goal: {state.get('goal_description', '(none)')}
  Goal type: {state.get('goal_type', '')}
  Company: {state.get('company_name') or '(not provided)'}
  Company description: {state.get('company_description') or '(not provided)'}
  Tracked keywords: {', '.join((state.get('keywords') or [])[:20]) or '(none)'}

POST (already passed keyword + semantic filters):
  Title: {state.get('post_title', '')}
  Content: {str(state.get('post_content', ''))[:1800]}
  Author: {state.get('post_author', 'unknown')}
  Community: r/{state.get('post_subreddit', '')}
  Matched keyword: "{state.get('matched_keyword', '')}"
  Stage-2 semantic score: {sem_display}
  Matched snippet: {state.get('matched_snippet', '')[:300]}

Decide: is this post an actionable lead for the project?"""


# ── Graph nodes (pure functions on state) ────────────────────────────────────

async def retrieve_context_node(state: LeadAgentState) -> LeadAgentState:
    """RAG-style context retrieval.

    Builds the project brief the grader will reason over. When embeddings
    are available, the project's context chunks (goal, company, keywords)
    are ranked by similarity to the post and the brief emphasizes the most
    relevant slices — a compact retrieval step over the project knowledge.
    """
    chunks = [
        ("goal", state.get("goal_description", "")),
        ("company", " ".join(filter(None, [
            state.get("company_name") or "",
            state.get("company_description") or "",
        ]))),
        ("keywords", "Tracked keywords: " + ", ".join(state.get("keywords") or [])),
    ]
    post_text = f"{state.get('post_title', '')} {state.get('post_content', '')}"

    try:
        from app.ai.embeddings import get_embedder, cosine_similarity
        embedder = get_embedder()
        if embedder is not None:
            post_vec = embedder.encode(post_text)
            scored = []
            for name, chunk in chunks:
                if not chunk.strip():
                    continue
                scored.append((cosine_similarity(post_vec, embedder.encode(chunk)), name, chunk))
            scored.sort(reverse=True)
            # The brief leads with the most relevant context slice
            brief = " | ".join(f"[{name}] {chunk}" for _, name, chunk in scored)
            return {**state, "project_brief": brief}
    except Exception as e:  # embeddings optional
        logger.debug("Context retrieval fell back to static brief: %s", e)

    brief = " | ".join(f"[{name}] {chunk}" for _, name, chunk in chunks if chunk.strip())
    return {**state, "project_brief": brief}


async def keyword_check_node(state: LeadAgentState) -> LeadAgentState:
    """Defensive re-verification that the Stage-1 keyword still appears in
    the post (guards against race between scrape time and grading time)."""
    kw = (state.get("matched_keyword") or "").lower()
    text = f"{state.get('post_title', '')} {state.get('post_content', '')}".lower()
    ok = (not kw) or (kw in text) or any(
        k.lower() in text for k in (state.get("keywords") or [])
    )
    return {**state, "keyword_ok": bool(ok)}


async def semantic_check_node(state: LeadAgentState) -> LeadAgentState:
    """Reads the Stage-2 semantic score. (Kept as an explicit graph node so
    the decision boundary is visible in the workflow.)"""
    return state


async def decide_node(state: LeadAgentState, llm_config=None) -> LeadAgentState:
    """Blends stage scores with the LLM verdict. Runs after llm_grade."""
    semantic = _clamp01(state.get("semantic_score"))
    confidence = _clamp01(state.get("confidence", 0.0))
    is_lead = bool(state.get("is_lead", False))

    if not is_lead:
        # Rejected by LLM — final score decays toward the semantic estimate
        final = min(1.0, 0.30 * semantic + 0.20 * confidence)
        return {**state, "confidence": confidence,
                "reason": state.get("reason", "") or "Not an actionable lead",
                "is_lead": False, "final_score": round(final, 4)}

    # LLM confirmed a lead — blend semantic + LLM confidence
    final = min(1.0, 0.45 * semantic + 0.55 * (0.55 + 0.45 * confidence))
    return {**state, "is_lead": True, "confidence": confidence,
            "reason": state.get("reason", ""),
            "final_score": round(max(final, 0.72), 4)}  # confirmed leads floor at 72


# ── Graph assembly ───────────────────────────────────────────────────────────

def build_lead_agent_graph():
    """Compiles the LangGraph StateGraph. Returns None when langgraph
    is not installed (caller then uses the sequential fallback)."""
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None

    async def _llm_grade_node(state: LeadAgentState) -> LeadAgentState:
        """Graph wrapper — pulls the BYO-LLM config stashed in state."""
        cfg = state.get("_llm_config")
        return await call_llm_grade(state, cfg)

    g = StateGraph(LeadAgentState)
    g.add_node("retrieve_context", retrieve_context_node)
    g.add_node("keyword_check", keyword_check_node)
    g.add_node("semantic_check", semantic_check_node)
    g.add_node("llm_grade", _llm_grade_node)
    g.add_node("decide", decide_node)

    g.set_entry_point("retrieve_context")
    g.add_edge("retrieve_context", "keyword_check")
    g.add_edge("keyword_check", "semantic_check")

    # Cost-control router: skip the LLM when semantic score is clearly below
    # the project's threshold — the post was already weak at Stage 2.
    def route_after_semantic(state: LeadAgentState) -> str:
        if state.get("keyword_ok") is False:
            return "decide"          # keyword race — go straight to decide (reject path)
        semantic = state.get("semantic_score")
        threshold = float(state.get("llm_threshold") or 0.7)
        if isinstance(semantic, (int, float)) and semantic + 0.18 < threshold:
            return "decide"          # too weak — don't burn LLM tokens
        return "llm_grade"

    g.add_conditional_edges(
        "semantic_check",
        route_after_semantic,
        {"llm_grade": "llm_grade", "decide": "decide"},
    )
    g.add_edge("llm_grade", "decide")
    g.add_edge("decide", END)

    return g.compile()


_compiled_graph = None


async def run_lead_agent(
    state: LeadAgentState,
    llm_config: Optional[Dict[str, Any]] = None,
) -> LeadAgentState:
    """Entry point used by Stage 3.

    Executes the LangGraph agent when available; otherwise runs the exact
    same steps sequentially (httpx LLM call included). Output contract is
    identical either way:

        {is_lead: bool, confidence: 0..1, reason: str, final_score: 0..1}
    """
    global _compiled_graph
    enriched = {**state, "_llm_config": llm_config}

    try:
        if _compiled_graph is None:
            _compiled_graph = build_lead_agent_graph()
        if _compiled_graph is not None:
            result = await _compiled_graph.ainvoke(enriched)
            result.pop("_llm_config", None)
            result.pop("project_brief", None)
            return result
    except Exception as e:
        logger.warning("Lead agent graph path failed (%s) — using sequential fallback", e)

    # ── Sequential fallback (no langgraph, same semantics) ──────────────────
    prepared = await retrieve_context_node(
        await semantic_check_node(await keyword_check_node(enriched))
    )
    cfg = prepared.pop("_llm_config", None)

    semantic = prepared.get("semantic_score")
    threshold = float(prepared.get("llm_threshold") or 0.7)
    if prepared.get("keyword_ok") is False or (
        isinstance(semantic, (int, float)) and semantic + 0.18 < threshold
    ):
        return await decide_node({**prepared, "is_lead": False, "confidence": 0.2,
                                  "reason": "Below threshold before LLM (cost control)"})

    graded = await call_llm_grade(prepared, cfg)
    return await decide_node(graded)
