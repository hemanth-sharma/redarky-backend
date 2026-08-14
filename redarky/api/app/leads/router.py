"""
app/leads/router.py

CRM endpoints for high-intent leads.

  GET    /leads                      → paginated list (filter by project, status)
  GET    /leads/{id}                 → lead detail
  PATCH  /leads/{id}/status          → update status (new/contacted/won/lost/...)
  PATCH  /leads/{id}/notes           → update user notes
  DELETE /leads/{id}                 → delete a lead
  GET    /projects/{project_id}/leads/stats  → counts by status (dashboard)

Users CANNOT manually create leads — only the Stage-3 LLM filter creates them.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.leads.schemas import (
    LeadResponse, LeadStatusUpdate, LeadNotesUpdate, LeadStats, LeadStatusEnum,
)
from app.leads import service
from app.projects import service as project_service
from app.utils.exceptions import NotFoundException

router = APIRouter(tags=["Leads"])


@router.get("/leads", response_model=List[LeadResponse])
async def read_leads(
    project_id: Optional[uuid.UUID] = None,
    status: Optional[LeadStatusEnum] = Query(default=None, alias="status"),
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List leads for the current user. Optional filters:
    - project_id: scope to a project
    - status: only leads with this status ("new" / "reviewed" / "contacted" / ...)
    """
    leads, _total = await service.get_leads(
        db=db,
        owner_id=current_user.id,
        project_id=project_id,
        status=status.value if status else None,
        page=page,
        page_size=page_size,
    )
    # Enrich with denormalized post snapshot
    return [await service.to_lead_response(db=db, lead=lead) for lead in leads]


@router.get("/leads/{id}", response_model=LeadResponse)
async def read_lead(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        lead = await service.get_lead(db=db, lead_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.to_lead_response(db=db, lead=lead)


@router.patch("/leads/{id}/status", response_model=LeadResponse)
async def update_lead_status(
    id: uuid.UUID,
    status_in: LeadStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Updates a lead's status. Auto-sets:
      - contacted_at when status → contacted
      - closed_at when status → won / lost
    """
    try:
        lead = await service.get_lead(db=db, lead_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    lead = await service.update_lead_status(db=db, lead=lead, status_in=status_in)
    return await service.to_lead_response(db=db, lead=lead)


@router.patch("/leads/{id}/notes", response_model=LeadResponse)
async def update_lead_notes(
    id: uuid.UUID,
    notes_in: LeadNotesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Adds or replaces the user's free-text notes on a lead."""
    try:
        lead = await service.get_lead(db=db, lead_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    lead = await service.update_lead_notes(db=db, lead=lead, notes_in=notes_in)
    return await service.to_lead_response(db=db, lead=lead)


@router.delete("/leads/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lead(
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        lead = await service.get_lead(db=db, lead_id=id, owner_id=current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    await service.delete_lead(db=db, lead=lead)
    return None


# ── Dashboard stats ──────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/leads/stats", response_model=LeadStats)
async def lead_stats(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated lead counts by status — for the dashboard."""
    try:
        await project_service.get_project(
            db=db, project_id=project_id, owner_id=current_user.id
        )
    except NotFoundException:
        raise HTTPException(status_code=404, detail="Project not found or unauthorized")
    return await service.get_lead_stats(db=db, project_id=project_id)
