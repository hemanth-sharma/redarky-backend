"""
app/sources/router.py

Monitored sources (subreddits / accounts / pages) for a project.

  POST   /projects/{project_id}/sources          → add source to project
  GET    /projects/{project_id}/sources          → list project's sources
  DELETE /projects/{project_id}/sources/{source_id}  → remove from project

The same source can be shared across many projects — adding/removing here
just creates/removes the ProjectSource link and adjusts subscriber_count
on the global MonitoredSource row.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.sources.schemas import (
    SourceAddRequest, MonitoredSourceResponse,
)
from app.sources import service as sources_service
from app.projects import service as project_service
from app.utils.exceptions import NotFoundException

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["Sources"])


@router.post("", response_model=MonitoredSourceResponse, status_code=status.HTTP_201_CREATED)
async def add_source(
    project_id: uuid.UUID,
    payload: SourceAddRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Adds a source (subreddit / account / page) to a project.

    Internally:
      1. Verifies project ownership
      2. Looks up (or creates) the global MonitoredSource row
      3. Creates a ProjectSource link
      4. Increments subscriber_count
      5. Flips is_active=True if this is the first subscriber
    """
    try:
        await project_service.get_project(
            db=db, project_id=project_id, owner_id=current_user.id
        )
    except NotFoundException:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    if not payload.identifier.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Source identifier cannot be empty",
        )

    return await sources_service.add_source_to_project(
        db=db, project_id=project_id, source_in=payload,
    )


@router.get("", response_model=List[MonitoredSourceResponse])
async def list_project_sources(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists every source the project is watching."""
    try:
        await project_service.get_project(
            db=db, project_id=project_id, owner_id=current_user.id
        )
    except NotFoundException:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")
    return await sources_service.get_project_sources(db=db, project_id=project_id)


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source(
    project_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Removes a source from a project. If subscriber_count hits 0, the source
    is auto-deactivated (Celery Beat will skip it on the next batch).
    """
    try:
        await project_service.get_project(
            db=db, project_id=project_id, owner_id=current_user.id
        )
    except NotFoundException:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")

    # Look up the source so we can pass source_type + identifier to the service
    from sqlalchemy import select
    from app.models import MonitoredSource
    result = await db.execute(
        select(MonitoredSource).where(MonitoredSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    await sources_service.remove_source_from_project(
        db=db,
        project_id=project_id,
        source_type=source.source_type,
        identifier=source.identifier,
    )
    return None
