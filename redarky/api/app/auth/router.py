"""
app/auth/router.py

Auth endpoints — register, login, refresh.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import (
    LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserResponse,
)
from app.auth.service import login_user, register_user, get_user_by_id
from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import get_db
from app.security import create_token, decode_token
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await register_user(db, payload)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    return await login_user(db, payload)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest) -> TokenPair:
    """
    Exchange a refresh token for a new access + refresh pair.
    Validates the refresh token type explicitly.
    """
    try:
        token_payload = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if token_payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong token type — expected refresh token",
        )

    user_id = token_payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed refresh token",
        )

    return TokenPair(
        access_token=create_token(user_id, "access", settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        refresh_token=create_token(user_id, "refresh", settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> User:
    """Returns the currently authenticated user's profile."""
    return current_user
