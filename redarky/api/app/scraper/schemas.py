"""
app/scraper/schemas.py

Schemas for the scraper domain — covers:
  1. Outbound: what FastAPI sends to the Go scraper (/scrape)
  2. Inbound: what the Go scraper returns
  3. Webhook: the body the Go scraper POSTs back to /ingestion/reddit
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Outbound: payload sent to Go scraper ─────────────────────────────────────
class ScraperPayload(BaseModel):
    """
    Payload FastAPI sends to the Go scraper /scrape endpoint.

    Built by the scraper service from:
      - All unique "include" keywords across active projects
      - All active MonitoredSource rows (subscriber_count > 0)

    Go scraper uses `keywords` for Reddit global search (sorted by new)
    and `subreddits` for direct subreddit pulls. Both run in one batch.
    """
    keywords: List[str] = Field(default_factory=list)
    subreddits: List[str] = Field(default_factory=list)
    sort: str = "new"                       # "new" | "hot" | "top"
    since_timestamp: Optional[int] = None   # Unix epoch; if None, Go uses default
    include_comments: bool = True           # search inside comments too


class ScraperRunRequest(BaseModel):
    """Manual trigger from frontend (mostly for testing)."""
    project_id: Optional[UUID] = None       # if None, runs shared batch
    since_timestamp: Optional[int] = None
    backfill: bool = False                  # 30-day backfill


# ── Inbound: what Go scraper returns ─────────────────────────────────────────
class ScrapedItem(BaseModel):
    """
    Normalised record returned by the Go scraper.
    Must match Go's internal struct exactly.
    """
    model_config = ConfigDict(extra="ignore")

    source: str                              # "reddit"
    external_id: str                         # "t3_abc123"
    title: str = ""
    content: str = ""
    author: str = "unknown"
    url: str
    score: int = 0
    comments_count: int = 0
    post_type: str = "post"                  # "post" | "comment"
    subreddit: str = ""
    created_at_platform: Optional[int] = None  # Unix timestamp


class SourceError(BaseModel):
    source: str
    query: str
    message: str


class GoScrapeResult(BaseModel):
    """Full response from POST /scrape on the Go service."""
    items: List[ScrapedItem] = []
    errors: List[SourceError] = []


# ── Webhook: Go scraper POSTs back to /ingestion/reddit ──────────────────────
class RunStats(BaseModel):
    total_items: int = 0
    posts: int = 0
    comments: int = 0
    request_count: int = 0
    duration_ms: int = 0
    errors: List[str] = []


class ScraperWebhookPayload(BaseModel):
    """
    Body the Go scraper sends to /ingestion/reddit after a batch completes.
    Decoupled from the manual /scrape call — Celery Beat triggers Go
    directly, and Go reports back via this webhook.
    """
    model_config = ConfigDict(extra="ignore")

    scraper_run_id: str = ""    # Correlates with scraper_runs.id
    keywords: List[str] = []
    subreddits: List[str] = []
    items: List[ScrapedItem] = []
    stats: RunStats = RunStats()


# ── ScraperRun response (for observability dashboard) ────────────────────────
class ScraperRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    total_items_pulled: int
    new_items_inserted: int
    duplicate_items_skipped: int
    payload_sent: dict
    error_message: Optional[str] = None
    created_at: datetime
