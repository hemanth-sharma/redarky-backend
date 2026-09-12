"""
app/integrations/router.py

  GET    /integrations            → list user's integrations
  POST   /integrations            → connect a channel (slack/email/discord/teams/whatsapp/llm)
  PATCH  /integrations/{id}       → update config / toggle active
  DELETE /integrations/{id}       → disconnect
  POST   /integrations/{id}/test  → send a test ping
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.integrations.schemas import (
    IntegrationCreate, IntegrationUpdate, IntegrationResponse, IntegrationTestResult,
)
from app.integrations import service
from app.utils.exceptions import NotFoundException

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await service.get_integrations(db, current_user.id)


@router.post("", response_model=IntegrationResponse, status_code=status.HTTP_201_CREATED)
async def create_integration(
    data: IntegrationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.integrations.schemas import ALLOWED_INTEGRATION_TYPES
    if data.type not in ALLOWED_INTEGRATION_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported integration type: {data.type}")
    return await service.create_integration(db, current_user.id, data)


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: uuid.UUID,
    data: IntegrationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        integration = await service.get_integration(db, integration_id, current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    return await service.update_integration(db, integration, data)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        integration = await service.get_integration(db, integration_id, current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    await service.delete_integration(db, integration)
    return None


@router.post("/{integration_id}/test", response_model=IntegrationTestResult)
async def test_integration(
    integration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        integration = await service.get_integration(db, integration_id, current_user.id)
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=e.message)
    ok, detail = await service.test_integration(db, integration)
    return IntegrationTestResult(ok=ok, detail=detail)
