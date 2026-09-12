"""
app/integrations/service.py

CRUD + outbound delivery for user integrations (slack/email/discord/teams/whatsapp/llm).

`notify_new_lead` is called by the matching pipeline (Stage 3) whenever a new
Lead is created — it fans the alert out to all of the owner's active channels.
Failures are logged and never break the pipeline.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.models import Integration
from app.integrations.schemas import IntegrationCreate, IntegrationUpdate
from app.utils.exceptions import NotFoundException, DomainException

logger = logging.getLogger("uvicorn.integrations")


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def get_integrations(db: AsyncSession, user_id: UUID) -> list[Integration]:
    result = await db.execute(
        select(Integration).where(Integration.user_id == user_id).order_by(Integration.created_at)
    )
    return list(result.scalars().all())


async def get_integration(db: AsyncSession, integration_id: UUID, user_id: UUID) -> Integration:
    row = (await db.execute(
        select(Integration).where(
            and_(Integration.id == integration_id, Integration.user_id == user_id)
        )
    )).scalar_one_or_none()
    if not row:
        raise NotFoundException(f"Integration {integration_id} not found")
    return row


async def get_active_by_type(db: AsyncSession, user_id: UUID, type_: str) -> Integration | None:
    return (await db.execute(
        select(Integration).where(
            and_(
                Integration.user_id == user_id,
                Integration.type == type_,
                Integration.is_active == True,  # noqa: E712
            )
        )
    )).scalar_one_or_none()


async def create_integration(db: AsyncSession, user_id: UUID, data: IntegrationCreate) -> Integration:
    if data.type == "llm":
        _validate_llm_config(data.config)
    integration = Integration(
        user_id=user_id,
        type=data.type,
        config=data.config,
        is_active=data.is_active,
    )
    db.add(integration)
    await db.commit()
    await db.refresh(integration)
    return integration


async def update_integration(
    db: AsyncSession, integration: Integration, data: IntegrationUpdate
) -> Integration:
    if data.config is not None:
        if integration.type == "llm":
            merged = {**(integration.config or {}), **data.config}
            _validate_llm_config(merged)
        integration.config = data.config
    if data.is_active is not None:
        integration.is_active = data.is_active
    await db.commit()
    await db.refresh(integration)
    return integration


async def delete_integration(db: AsyncSession, integration: Integration) -> None:
    await db.delete(integration)
    await db.commit()


def _validate_llm_config(config: dict) -> None:
    if not (config or {}).get("api_key"):
        raise DomainException("LLM integration requires an 'api_key' in config.")


# ── BYO-LLM resolution (used by Stage 3) ────────────────────────────────────

async def get_user_llm_config(db: AsyncSession, user_id: UUID) -> dict | None:
    """Returns {'api_key', 'base_url', 'model'} if the user has an active
    bring-your-own LLM integration, else None (platform default is used)."""
    integration = await get_active_by_type(db, user_id, "llm")
    if not integration:
        return None
    config = integration.config or {}
    if not config.get("api_key"):
        return None
    return {
        "api_key": config["api_key"],
        "base_url": config.get("base_url") or "https://api.openai.com/v1/chat/completions",
        "model": config.get("model") or "gpt-4o-mini",
    }


# ── Outbound delivery ───────────────────────────────────────────────────────

async def notify_new_lead(
    db: AsyncSession,
    owner_id: UUID,
    product_name: str,
    lead_intent_score: float,
    matched_keyword: str,
    url: str,
    title: str,
    reason: str = "",
) -> None:
    """Fans a new-lead alert out to all active webhook/email integrations.
    Never raises — alerting must not break the matching pipeline."""
    integrations = await get_integrations(db, owner_id)
    targets = [i for i in integrations if i.is_active and i.type != "llm"]
    if not targets:
        return

    score_pct = round((lead_intent_score or 0) * 100)
    text = (
        f"🔔 New Redarky lead for *{product_name}* — intent {score_pct}/100\n"
        f"Matched: \"{matched_keyword}\"\n"
        f"{title}\n"
        f"{reason[:300] if reason else ''}\n"
        f"{url}"
    )

    for integration in targets:
        try:
            await send_message(integration.type, integration.config or {}, text)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Lead alert to %s integration %s failed: %s",
                integration.type, integration.id, e,
            )


async def send_message(type_: str, config: dict, text: str) -> None:
    """Sends one message to one channel type. Raises on failure (caller handles)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        if type_ in ("slack", "teams", "whatsapp"):
            webhook = (config or {}).get("webhook_url")
            if not webhook:
                raise ValueError("webhook_url not configured")
            payload_key = "text"  # slack + teams incoming webhooks + generic whatsapp gateways
            await client.post(webhook, json={payload_key: text})
        elif type_ == "discord":
            webhook = (config or {}).get("webhook_url")
            if not webhook:
                raise ValueError("webhook_url not configured")
            # Discord webhooks truncate over 2000 chars
            await client.post(webhook, json={"content": text[:1900]})
        elif type_ == "email":
            to_email = (config or {}).get("to_email")
            if not to_email:
                raise ValueError("to_email not configured")
            await _send_email(to_email, "New Redarky lead 🎯", text)
        else:
            raise ValueError(f"Unsupported integration type: {type_}")


async def test_integration(db: AsyncSession, integration: Integration) -> tuple[bool, str]:
    """Sends a test ping. Returns (ok, detail)."""
    try:
        if integration.type == "llm":
            config = integration.config or {}
            ok, detail = await _test_llm(config)
        else:
            await send_message(
                integration.type,
                integration.config or {},
                "✅ This is a test message from Redarky — your integration works!",
            )
            ok, detail = True, "Test message delivered."
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"Test failed: {str(e)[:200]}"
    finally:
        integration.last_tested_at = datetime.now(timezone.utc)

    await db.commit()
    return ok, detail


async def _test_llm(config: dict) -> tuple[bool, str]:
    """Pings the BYO LLM endpoint with a tiny prompt."""
    api_key = (config or {}).get("api_key")
    if not api_key:
        return False, "Missing api_key."
    base_url = (config.get("base_url") or "https://api.openai.com/v1/chat/completions").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    model = config.get("model") or "gpt-4o-mini"

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
                "max_tokens": 5,
            },
        )
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"]
        return True, f"LLM responded: {str(content).strip()[:100]}"


async def _send_email(to_email: str, subject: str, body: str) -> None:
    from app.config import settings

    if not settings.SMTP_HOST:
        # No SMTP configured — log so local dev still "works"
        logger.info("[integrations] SMTP not configured. Would email %s: %s", to_email, subject)
        return

    import asyncio
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or "Redarky <no-reply@redarky.com>"
    msg["To"] = to_email

    def _send():
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], [to_email], msg.as_string())

    await asyncio.to_thread(_send)
