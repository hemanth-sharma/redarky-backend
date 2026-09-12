"""
app/feedback/router.py

Public feedback endpoints — email verification required before submission.

  POST /feedback/request-verification  {email}          → sends code
  POST /feedback/verify               {email, code}     → marks verified
  POST /feedback                      {email, message}  → stores feedback (verified emails only)
  GET  /feedback/status?email=...                       → is this email verified?

No auth required — the verified email IS the identity. If an Authorization
header is present, the feedback is additionally linked to the user account.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.feedback import service
from app.feedback.schemas import (
    VerificationRequest, VerificationVerify, FeedbackCreate,
    VerificationRequestResponse, VerificationVerifyResponse,
    FeedbackAccepted, VerificationStatus,
)
from app.utils.exceptions import DomainException

logger = logging.getLogger("uvicorn.feedback")

router = APIRouter(prefix="/feedback", tags=["Feedback"])


async def _optional_user_id(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """Best-effort: if a valid bearer token is present, link feedback to the user.
    Never fails the request — feedback must work for logged-out visitors too."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        from app.security import decode_token
        from app.models import User
        from sqlalchemy import select
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        return str(user.id) if user else None
    except Exception:
        return None


@router.post("/request-verification", response_model=VerificationRequestResponse)
async def request_verification(
    body: VerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        verification, dev_code = await service.request_verification(db, body.email)
    except DomainException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)

    return VerificationRequestResponse(
        email=body.email,
        expires_in_minutes=settings_code_minutes(),
        dev_code=dev_code,
    )


def settings_code_minutes() -> int:
    from app.config import settings as s
    return s.FEEDBACK_CODE_EXPIRE_MINUTES


@router.post("/verify", response_model=VerificationVerifyResponse)
async def verify(
    body: VerificationVerify,
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.verify_code(db, body.email, body.code)
    except DomainException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    return VerificationVerifyResponse(
        email=body.email,
        verified=True,
        message="Email verified — you can now submit feedback.",
    )


@router.get("/status", response_model=VerificationStatus)
async def verification_status(
    email: str,
    db: AsyncSession = Depends(get_db),
):
    verified = await service.is_email_verified(db, email)
    return VerificationStatus(email=email, verified=verified)


@router.post("", response_model=FeedbackAccepted, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    user_id: Optional[str] = Depends(_optional_user_id),
):
    try:
        feedback = await service.submit_feedback(
            db, body, user_id=user_id
        )
    except DomainException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    return FeedbackAccepted(
        id=feedback.id,
        email=feedback.email,
        category=feedback.category,
        created_at=feedback.created_at,
    )
