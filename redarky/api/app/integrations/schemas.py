"""
app/integrations/schemas.py
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_INTEGRATION_TYPES = {"slack", "email", "discord", "teams", "whatsapp", "llm"}


class IntegrationCreate(BaseModel):
    type: str = Field(pattern="^(slack|email|discord|teams|whatsapp|llm)$")
    config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class IntegrationUpdate(BaseModel):
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class IntegrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    config: Dict[str, Any]
    is_active: bool
    last_tested_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IntegrationTestResult(BaseModel):
    ok: bool
    detail: str


class LeadAlertPayload(BaseModel):
    """Shape of the push payload sent to webhook integrations."""
    product_name: str
    intent_score: float
    matched_keyword: str
    url: str
    title: str
    reason: str = ""
