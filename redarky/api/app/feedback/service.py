"""
app/feedback/service.py

Implements the strict verified-email feedback flow:
  request-verification → verify → submit feedback

Email delivery: if SMTP is configured (SMTP_HOST etc.), the code is sent
by email. Otherwise the code is logged, and in non-production environments
it is also returned in the API response (`dev_code`) so the flow can be
tested end-to-end locally.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.feedback.models import Feedback, FeedbackVerification
from app.feedback.schemas import FeedbackCreate
from app.utils.exceptions import DomainException

logger = logging.getLogger("uvicorn.feedback")

# ── Rate limits ─────────────────────────────────────────────────────────────
MAX_VERIFICATION_REQUESTS_PER_HOUR = 5
MAX_VERIFY_ATTEMPTS = 5


def _hash_code(email: str, code: str) -> str:
    """Deterministic hash binding the code to the email (salted by SECRET_KEY)."""
    salt = settings.SECRET_KEY or "redarky"
    return hashlib.sha256(f"{salt}:{email.lower()}:{code}".encode()).hexdigest()


def _generate_code() -> str:
    """Cryptographically random 6-digit code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _is_smtp_configured() -> bool:
    return bool(settings.SMTP_HOST)


def should_return_dev_code() -> bool:
    """dev_code is returned only when there is no way to email the code
    AND we're not running in production."""
    return (not _is_smtp_configured()) and (settings.ENVIRONMENT or "dev").lower() not in (
        "production", "prod",
    )


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """SQLite returns offset-naive datetimes; Postgres returns aware ones.
    Normalizes to aware so comparisons never blow up on either backend."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def request_verification(db: AsyncSession, email: str) -> tuple[FeedbackVerification, str | None]:
    """Creates a fresh verification code for the email.

    Returns (verification_row, dev_code_or_None).
    Raises DomainException on rate limit.
    """
    email = email.lower().strip()

    # Rate limit: max N requests per email per hour
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_count = await db.scalar(
        select(func.count(FeedbackVerification.id)).where(
            and_(
                FeedbackVerification.email == email,
                FeedbackVerification.created_at >= hour_ago,
            )
        )
    )
    if (recent_count or 0) >= MAX_VERIFICATION_REQUESTS_PER_HOUR:
        raise DomainException(
            "Too many verification requests for this email. Please try again later."
        )

    code = _generate_code()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.FEEDBACK_CODE_EXPIRE_MINUTES
    )

    # Invalidate previous unverified codes for this email
    old_rows = (await db.execute(
        select(FeedbackVerification).where(
            and_(
                FeedbackVerification.email == email,
                FeedbackVerification.verified_at.is_(None),
            )
        )
    )).scalars().all()
    for row in old_rows:
        await db.delete(row)

    verification = FeedbackVerification(
        email=email,
        code_hash=_hash_code(email, code),
        expires_at=expires_at,
    )
    db.add(verification)
    await db.commit()
    await db.refresh(verification)

    # Send the code (or log it)
    dev_code: str | None = None
    if _is_smtp_configured():
        try:
            await _send_code_email(email, code)
        except Exception as e:  # pragma: no cover
            logger.error("Failed to send verification email to %s: %s", email, e)
            raise DomainException("Failed to send verification email. Please try again.")
    else:
        logger.info(
            "[feedback] SMTP not configured — verification code for %s: %s "
            "(set SMTP_HOST to enable real delivery)", email, code,
        )
        if should_return_dev_code():
            dev_code = code

    return verification, dev_code


async def verify_code(db: AsyncSession, email: str, code: str) -> FeedbackVerification:
    """Verifies the 6-digit code. Raises DomainException on any failure."""
    email = email.lower().strip()

    row = (await db.execute(
        select(FeedbackVerification)
        .where(FeedbackVerification.email == email)
        .order_by(FeedbackVerification.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not row:
        raise DomainException("No verification pending for this email. Request a new code.")

    if row.verified_at is not None:
        return row  # already verified — idempotent

    if _ensure_aware(row.expires_at) < datetime.now(timezone.utc):
        raise DomainException("Verification code expired. Request a new one.")

    if row.attempts >= MAX_VERIFY_ATTEMPTS:
        raise DomainException("Too many incorrect attempts. Request a new code.")

    if row.code_hash != _hash_code(email, code.strip()):
        row.attempts += 1
        await db.commit()
        remaining = MAX_VERIFY_ATTEMPTS - row.attempts
        raise DomainException(
            f"Incorrect code. {remaining} attempt(s) remaining."
            if remaining > 0 else "Too many incorrect attempts. Request a new code."
        )

    row.verified_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def is_email_verified(db: AsyncSession, email: str) -> bool:
    email = email.lower().strip()
    row = (await db.execute(
        select(FeedbackVerification)
        .where(FeedbackVerification.email == email)
        .order_by(FeedbackVerification.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    return bool(row and row.verified_at is not None)


async def submit_feedback(
    db: AsyncSession, feedback_in: FeedbackCreate, user_id: UUID | None = None
) -> Feedback:
    """Stores feedback. STRICT: the email must be verified first."""
    email = feedback_in.email.lower().strip()

    if not await is_email_verified(db, email):
        raise DomainException(
            "Email not verified. Please verify your email before submitting feedback."
        )

    # Simple spam guard: max 10 feedbacks per email per day
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    recent = await db.scalar(
        select(func.count(Feedback.id)).where(
            and_(Feedback.email == email, Feedback.created_at >= day_ago)
        )
    )
    if (recent or 0) >= 10:
        raise DomainException("Daily feedback limit reached. Please try again tomorrow.")

    feedback = Feedback(
        email=email,
        message=feedback_in.message.strip(),
        category=feedback_in.category,
        user_id=user_id,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)

    logger.info("Feedback submitted by %s (category=%s)", email, feedback.category)
    return feedback


# ── Email delivery ──────────────────────────────────────────────────────────

async def _send_code_email(to_email: str, code: str) -> None:
    """Sends the verification code via SMTP (best-effort, non-blocking caller)."""
    import smtplib
    from email.mime.text import MIMEText

    subject = "Your Redarky feedback verification code"
    body = (
        f"Your verification code is: {code}\n\n"
        f"It expires in {settings.FEEDBACK_CODE_EXPIRE_MINUTES} minutes.\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or "Redarky <no-reply@redarky.com>"
    msg["To"] = to_email

    # smtplib is sync — run in a thread so we don't block the event loop
    import asyncio

    def _send():
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], [to_email], msg.as_string())

    await asyncio.to_thread(_send)
