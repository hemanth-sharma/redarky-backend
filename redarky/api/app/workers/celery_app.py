"""
app/workers/celery_app.py

Celery configuration. Three Beat schedules:
  1. Shared scraper run (every 30 min) — pulls data for ALL active projects
  2. Stage-3 LLM filter (every 10 min) — catches unprocessed high-score posts
  3. Nightly TTL cleanup (3 AM UTC) — deletes expired raw_posts + matched_posts

The shared scraper task flow (every 30 min):
  a) Aggregates all active keywords + subreddits across projects
  b) Calls Go scraper ONCE (shared scraping — saves API quota)
  c) Bulk-upserts items into raw_posts (dedup)
  d) Runs the 3-stage matching pipeline on new raw_post IDs
"""
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "redarky",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # important for I/O-bound tasks
)

# ── Beat schedule ────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Shared scraper run — pulls data for ALL active projects in one Go call
    "run-shared-scraper": {
        "task": "app.workers.scraper_tasks.run_shared_scraper_task",
        "schedule": settings.SCRAPER_INTERVAL_MINUTES * 60,  # seconds
    },
    # Stage 3 LLM filter — processes unprocessed high-score posts every 10 min.
    # Catches posts that scored high after the initial pipeline run, and
    # retries failed LLM calls.
    "run-llm-filter": {
        "task": "app.workers.matching_tasks.run_llm_filter_task",
        "schedule": settings.LLM_INTERNAL_MINUTES * 60,  # every 10 min
    },
    # Nightly TTL cleanup — deletes expired raw_posts + matched_posts
    "run-ttl-cleanup": {
        "task": "app.workers.tasks.run_cleanup_task",
        "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
    },
}

# Alias `celery` for backwards compatibility with code that imports `celery`
celery = celery_app

celery_app.autodiscover_tasks([
    "app.workers.scraper_tasks",
    "app.workers.matching_tasks",
    "app.workers.tasks",
])
