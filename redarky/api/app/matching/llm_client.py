"""
app/matching/llm_client.py

Thin wrapper around the LLM provider for Stage 3.

Uses OpenAI-compatible API (works with OpenAI, Anthropic via proxy,
or self-hosted vLLM). Keeps prompts in one place so we can A/B test.

The LLM is given:
  - Project goal + company context
  - Post title + content + matched_keyword + matched_snippet
And returns:
  - is_lead: bool
  - reason: str (one sentence explaining why)
"""
import logging
from typing import Tuple

import httpx

from app.config import settings
from app.models import MatchedPost, Project

logger = logging.getLogger("uvicorn.llm")

SYSTEM_PROMPT = """You are a lead-generation analyst. Your job is to decide if a social media post represents a HIGH-INTENT buying opportunity for the user's project.

You will be given:
  - PROJECT GOAL: what the user is trying to achieve
  - PROJECT CONTEXT: the user's company / product info
  - POST: a single Reddit post or comment

Return JSON with exactly two fields:
  {
    "is_lead": true | false,
    "reason": "one-sentence explanation of your decision"
  }

Rules:
- is_lead = TRUE only if the post shows active buying intent toward the user's product category or against the user's competitors.
- Examples of high intent: "looking for a tool to...", "alternative to <competitor>", "should I use <competitor> or...", "switching from <competitor>".
- Examples of LOW intent (return false): general discussion, complaints about the user's product, questions unrelated to buying.
- Be conservative — better to miss a weak lead than to flood the user with noise.
"""


class LLMLeadClassifier:
    """Calls an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self):
        if not settings.LLM_API_KEY:
            raise RuntimeError("LLM_API_KEY not configured")
        self.api_url = settings.LLM_API_URL
        self.api_key = settings.LLM_API_KEY
        self.model = settings.LLM_MODEL_NAME

    async def classify_lead(
        self, project: Project, post: MatchedPost
    ) -> Tuple[bool, str]:
        """Returns (is_lead, reason)."""
        user_prompt = self._build_user_prompt(project, post)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 200,
                        "response_format": {"type": "json_object"},
                    },
                )
                res.raise_for_status()
                data = res.json()
                content = data["choices"][0]["message"]["content"]

                # Parse JSON response
                import json
                parsed = json.loads(content)
                return bool(parsed.get("is_lead", False)), str(parsed.get("reason", ""))

        except Exception as e:
            logger.error("LLM classify_lead failed: %s", e)
            raise

    def _build_user_prompt(self, project: Project, post: MatchedPost) -> str:
        return f"""PROJECT GOAL: {project.goal_description}
PROJECT GOAL TYPE: {project.goal_type}
PROJECT COMPANY: {project.company_name or "(not provided)"}
PROJECT COMPANY DESCRIPTION: {project.company_description or "(not provided)"}

POST TITLE: {post.title}
POST CONTENT: {post.content[:1500]}
POST AUTHOR: {post.author}
POST SUBREDDIT: {post.subreddit}
MATCHED KEYWORD: {post.matched_keyword}
MATCHED SNIPPET: {post.matched_snippet}
INTENT SCORE (Stage 2): {post.intent_score}

Decide: is this post a high-intent lead for the project?"""
