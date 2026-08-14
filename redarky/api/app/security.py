"""
app/security.py

JWT helpers + password hashing.

Uses PyJWT (jwt.encode / jwt.decode) for token creation/verification,
and bcrypt for password hashing. The decode_token function returns the
full payload dict — type checking (access vs refresh) is the caller's job.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: str, token_type: str, expire_minutes: int) -> str:
    """Creates a signed JWT. token_type is "access" or "refresh"."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    """
    Decodes + verifies a JWT signature and expiration.
    Returns the full payload dict (including "sub", "type", "iat", "exp").
    Caller is responsible for checking the "type" field if needed.

    Raises jwt.InvalidTokenError (or subclass) on any failure.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
