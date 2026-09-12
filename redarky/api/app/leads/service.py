"""
app/leads/service.py

CRM operations for high-intent leads.

Lead creation is normally done by the matching engine (Stage 3).
Users only UPDATE leads (status, notes) — they don't create them.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.leads.models import Lead, LeadStatus
from app.leads.schemas import LeadCreate, LeadStatusUpdate, LeadNotesUpdate, LeadStats, LeadResponse
from app.models import MatchedPost, Project
from app.utils.exceptions import NotFoundException


async def get_leads(
    db: AsyncSession,
    owner_id: UUID,
    project_id: Optional[UUID] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Lead], int]:
    """Returns (leads, total_count). Filters by owner_id for security."""
    base = (
        select(Lead)
        .join(Project, Lead.project_id == Project.id)
        .where(Project.owner_id == owner_id)
    )
    count_base = (
        select(func.count(Lead.id))
        .join(Project, Lead.project_id == Project.id)
        .where(Project.owner_id == owner_id)
    )

    if project_id:
        base = base.where(Lead.project_id == project_id)
        count_base = count_base.where(Lead.project_id == project_id)
    if status:
        base = base.where(Lead.status == status)
        count_base = count_base.where(Lead.status == status)

    total = await db.scalar(count_base) or 0

    # Highest-intent first — the queue shows the strongest leads on top
    offset = (page - 1) * page_size
    base = base.order_by(Lead.intent_score.desc(), Lead.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(base)
    return list(result.scalars().all()), int(total)


async def get_lead(db: AsyncSession, lead_id: UUID, owner_id: UUID) -> Lead:
    result = await db.execute(
        select(Lead)
        .join(Project, Lead.project_id == Project.id)
        .where(Lead.id == lead_id, Project.owner_id == owner_id)
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise NotFoundException(f"Lead {lead_id} not found")
    return lead


async def create_lead(db: AsyncSession, lead_in: LeadCreate) -> Lead:
    """Called by the matching engine (Stage 3). Not exposed to users."""
    lead = Lead(
        project_id=lead_in.project_id,
        matched_post_id=lead_in.matched_post_id,
        status=LeadStatus.NEW.value,
        intent_score=lead_in.intent_score,
        url=lead_in.url,
        matched_keyword=lead_in.matched_keyword,
        llm_reason=lead_in.llm_reason,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead


async def update_lead_status(
    db: AsyncSession, lead: Lead, status_in: LeadStatusUpdate
) -> Lead:
    lead.status = status_in.status.value

    # Timestamp tracking
    if status_in.status.value == LeadStatus.CONTACTED.value and not lead.contacted_at:
        lead.contacted_at = datetime.now(timezone.utc)
    elif status_in.status.value in (LeadStatus.WON.value, LeadStatus.LOST.value):
        lead.closed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(lead)
    return lead


async def update_lead_notes(
    db: AsyncSession, lead: Lead, notes_in: LeadNotesUpdate
) -> Lead:
    lead.notes = notes_in.notes
    await db.commit()
    await db.refresh(lead)
    return lead


async def delete_lead(db: AsyncSession, lead: Lead) -> None:
    await db.delete(lead)
    await db.commit()


# ── Dashboard stats ──────────────────────────────────────────────────────────
async def get_lead_stats(db: AsyncSession, project_id: UUID) -> LeadStats:
    counts = {}
    for status in ["new", "reviewed", "contacted", "won", "lost", "ignored"]:
        count = await db.scalar(
            select(func.count(Lead.id)).where(
                Lead.project_id == project_id,
                Lead.status == status,
            )
        )
        counts[status] = int(count or 0)

    total = await db.scalar(
        select(func.count(Lead.id)).where(Lead.project_id == project_id)
    )

    return LeadStats(
        project_id=project_id,
        total=int(total or 0),
        **counts,
    )


# ── Lead → Response mapper (with denormalized post snapshot) ─────────────────
async def to_lead_response(db: AsyncSession, lead: Lead) -> LeadResponse:
    """Enriches a Lead with post snapshot fields for the frontend list."""
    post = await db.scalar(
        select(MatchedPost).where(MatchedPost.id == lead.matched_post_id)
    )
    resp = LeadResponse.model_validate(lead)
    if post:
        resp.post_title = post.title
        resp.post_author = post.author
        resp.post_subreddit = post.subreddit
    return resp
