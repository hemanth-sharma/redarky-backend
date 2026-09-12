"""
app/scraper/router.py

Scraper endpoints — manual triggers + observability.

  POST /scraper/run             → manually trigger a shared scraper batch
  GET  /scraper/runs            → list recent scraper runs (debug dashboard)
  GET  /scraper/runs/{run_id}   → view a specific scraper run's payload + stats

For recurring polling, Celery Beat fires `run_shared_scraper_task` every
30 min automatically — no need to call /scraper/run.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.scraper.schemas import ScraperRunRequest, ScraperRunResponse
from app.scraper import service as scraper_service
from app.workers.scraper_tasks import run_manual_scrape_task
from app.utils.exceptions import NotFoundException

router = APIRouter(prefix="/scraper", tags=["Scraper"])


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_scraper(
    payload: ScraperRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually triggers a shared scraper batch (mostly for testing or
    "Fetch now" button on the dashboard).

    Returns immediately — the actual Go scraper call + ingestion + matching
    happens in the Celery worker.
    """
    # If project_id is given, verify ownership
    if payload.project_id:
        from app.projects import service as project_service
        try:
            await project_service.get_project(
                db=db, project_id=payload.project_id, owner_id=current_user.id
            )
        except NotFoundException:
            raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    # Fire the shared scraper task (async)
    job = run_manual_scrape_task.delay(project_id=str(payload.project_id) if payload.project_id else None)

    return {
        "status": "accepted",
        "mode": "backfill" if payload.backfill else "live",
        "job_id": job.id,
        "message": "Shared scraper batch started. Check /scraper/runs for results.",
    }


@router.get("/runs", response_model=list[ScraperRunResponse])
async def list_scraper_runs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists recent pipeline runs with per-run matched-post and lead counts.
    `limit` is clamped server-side to 1..50 so a stray large value can never
    flood the client with historical rows."""
    return await scraper_service.list_scraper_runs(
        db=db, limit=limit, with_matched_counts=True
    )


@router.get("/runs/{run_id}", response_model=ScraperRunResponse)
async def get_scraper_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns a single scraper run's payload + stats — lets you see exactly
    what keywords/subreddits were sent to Go and how many items came back."""
    try:
        return await scraper_service.get_scraper_run(db=db, run_id=run_id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
