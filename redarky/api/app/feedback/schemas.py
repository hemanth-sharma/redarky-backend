"""
app/feedback/schemas.py
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict


class VerificationRequest(BaseModel):
    email: EmailStr


class VerificationVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)


class FeedbackCreate(BaseModel):
    email: EmailStr
    message: str = Field(min_length=5, max_length=5000)
    category: str = Field(default="general", pattern="^(general|bug|feature_request|other)$")


class VerificationStatus(BaseModel):
    email: EmailStr
    verified: bool


class FeedbackAccepted(BaseModel):
    id: UUID
    email: EmailStr
    category: str
    created_at: datetime


class VerificationRequestResponse(BaseModel):
    """Response for request-verification.

    `dev_code` is ONLY populated when SMTP is not configured AND the
    environment is non-production — so the flow is testable locally
    without a mail server. In production it is always None.
    """
    email: EmailStr
    expires_in_minutes: int
    dev_code: Optional[str] = None


class VerificationVerifyResponse(BaseModel):
    email: EmailStr
    verified: bool
    message: str = ""
