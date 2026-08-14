"""
app/ingestion/schemas.py

Pydantic models for the ingestion domain — the /ingestion/reddit
webhook endpoint and the bulk-upsert contract.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.scraper.schemas import ScrapedItem, RunStats


# ── Webhook payload (alias for clarity in router code) ───────────────────────
class IngestionWebhookPayload(BaseModel):
    """
    Body the Go scraper POSTs to /ingestion/reddit.

    Same shape as ScraperWebhookPayload — kept as a separate type so
    the ingestion router can evolve independently of the scraper schema.
    """
    model_config = ConfigDict(extra="ignore")

    scraper_run_id: str = ""
    keywords: List[str] = []
    subreddits: List[str] = []
    items: List[ScrapedItem] = []
    stats: RunStats = RunStats()


# ── Bulk upsert result (returned by service to router / worker) ──────────────
class IngestionResult(BaseModel):
    """Returned after bulk-upserting a batch of items into raw_posts."""
    scraper_run_id: Optional[UUID] = None
    total_received: int
    new_inserted: int
    duplicates_skipped: int
    new_raw_post_ids: List[UUID]  # IDs of newly inserted rows — fed to matcher


# ── Cleanup result ────────────────────────────────────────────────────────────
class CleanupResult(BaseModel):
    deleted_raw_posts: int = 0
    deleted_matched_posts: int = 0
