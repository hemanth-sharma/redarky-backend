"""
app/matching/router.py

Admin/debug endpoints for the 3-stage matching pipeline.

Most users never touch these — the pipeline runs automatically via Celery.
Useful for:
  - Re-scoring all matched posts after upgrading the embedding model
  - Re-running the LLM filter on unprocessed high-score posts
  - Inspecting pipeline state during debugging

  POST /matching/rerun-stage2   → re-score all matched posts (Stage 2)
  POST /matching/rerun-stage3   → re-run LLM filter on unprocessed posts (Stage 3)
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.workers.matching_tasks import (
    run_llm_filter_task,
    run_semantic_rescore_task,
)

router = APIRouter(prefix="/matching", tags=["Matching"])


@router.post("/rerun-stage2", status_code=status.HTTP_202_ACCEPTED)
async def rerun_stage2(
    current_user: User = Depends(get_current_user),
):
    """
    Queues a Stage-2 rescore across all matched posts.
    Use after:
      - Upgrading the embedding model
      - Changing INTENT_PHRASES in app/matching/service.py
      - User changed their project goal_description (affects profile embedding)
    """
    job = run_semantic_rescore_task.delay()
    return {
        "status": "accepted",
        "job_id": job.id,
        "message": "Stage-2 rescore queued. Posts will be re-scored within a few minutes.",
    }


@router.post("/rerun-stage3", status_code=status.HTTP_202_ACCEPTED)
async def rerun_stage3(
    current_user: User = Depends(get_current_user),
):
    """
    Queues a Stage-3 LLM filter pass on unprocessed high-score matched posts.
    Use after:
      - LLM was offline during the initial pipeline run
      - LLM_API_KEY was just configured
      - LLM threshold was lowered on a project
    """
    job = run_llm_filter_task.delay()
    return {
        "status": "accepted",
        "job_id": job.id,
        "message": "Stage-3 LLM filter queued. Will process up to 50 unprocessed posts.",
    }
