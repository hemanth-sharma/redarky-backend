"""
app/projects/router.py

Project CRUD + pipeline activation + dashboard stats.

User flow mapping:
  POST   /projects                → user creates a project (with goal, company)
  GET    /projects                → list user's projects
  GET    /projects/{id}           → view a project
  PUT    /projects/{id}           → edit goal / company / retention
  DELETE /projects/{id}           → delete
  POST   /projects/{id}/activate  → frontend "Activate Pipeline" button
  POST   /projects/{id}/deactivate
  GET    /projects/{id}/stats     → dashboard counts (matched_posts, leads, etc.)
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.projects.schemas import (
    ProjectCreate, ProjectUpdate, ProjectResponse, PipelineActivate, ProjectStats,
)
from app.projects import service
from app.utils.exceptions import NotFoundException

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.create_project(db=db, project_in=project_in, owner_id=current_user.id)


@router.get("", response_model=List[ProjectResponse])
async def read_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.get_projects(db=db, owner_id=current_user.id)


@router.get("/{id}", response_model=ProjectResponse)
async def read_project(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.put("/{id}", response_model=ProjectResponse)
async def update_project(
    id: uuid.UUID,
    project_in: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        project = await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.update_project(db=db, project=project, project_in=project_in)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        project = await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    await service.delete_project(db=db, project=project)
    return None


# ── Pipeline activation (frontend button) ────────────────────────────────────
@router.post("/{id}/activate", response_model=ProjectResponse)
async def activate_pipeline(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Flips is_pipeline_active=True. Celery Beat will pick this project up on
    the next 30-min cycle and include its keywords + sources in the shared
    Go scraper batch.
    """
    try:
        project = await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.activate_pipeline(db=db, project=project)


@router.post("/{id}/deactivate", response_model=ProjectResponse)
async def deactivate_pipeline(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pauses pipeline for this project. Does NOT remove keywords/sources
    from the shared MonitoredSource registry — other projects may share them."""
    try:
        project = await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.deactivate_pipeline(db=db, project=project)


# ── Dashboard stats ──────────────────────────────────────────────────────────
@router.get("/{id}/stats", response_model=ProjectStats)
async def project_stats(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated counts for the project dashboard — matched posts, leads,
    unactioned leads, active keywords/sources, last scraper run time."""
    try:
        project = await service.get_project(db=db, project_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.get_project_stats(db=db, project=project)
