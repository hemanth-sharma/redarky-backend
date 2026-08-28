"""
app/main.py

Redarky API v2 — Social Listening Lead Generation Platform.

Includes all routers from the 9 domains:
  auth, projects, keywords, sources, scraper, ingestion, matching, posts, leads
"""
import os
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.projects.router import router as project_router
from app.keywords.router import router as keyword_router
from app.sources.router import router as sources_router
from app.scraper.router import router as scraper_router
from app.ingestion.router import router as ingestion_router
from app.matching.router import router as matching_router
from app.posts.router import router as post_router
from app.leads.router import router as lead_router

from app.workers.celery_app import celery

from app.utils.exceptions import (
    NotFoundException, UnauthorizedException, DomainException, ConflictException,
)
from app.config import settings


app = FastAPI(
    title="Redarky API",
    description="Social Listening Lead Generation Platform Backend — v2",
    version="2.0.0",
)

# CORS Middleware Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── Standardized error responses ─────────────────────────────────────────────
def create_error_response(status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"type": error_type, "message": message},
        },
    )


@app.exception_handler(NotFoundException)
async def not_found_handler(request: Request, exc: NotFoundException):
    return create_error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        error_type="RESOURCE_NOT_FOUND",
        message=exc.message,
    )


@app.exception_handler(UnauthorizedException)
async def unauthorized_handler(request: Request, exc: UnauthorizedException):
    return create_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        error_type="UNAUTHORIZED",
        message=exc.message,
    )


@app.exception_handler(ConflictException)
async def conflict_handler(request: Request, exc: ConflictException):
    return create_error_response(
        status_code=status.HTTP_409_CONFLICT,
        error_type="CONFLICT",
        message=exc.message,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    msg = (
        f"Validation failed: {errors[0]['loc'][-1]} - {errors[0]['msg']}"
        if errors else "Invalid request payload."
    )
    return create_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_type="VALIDATION_ERROR",
        message=msg,
    )


@app.exception_handler(DomainException)
async def domain_exception_handler(request: Request, exc: DomainException):
    return create_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        error_type="DOMAIN_ERROR",
        message=exc.message,
    )


@app.exception_handler(Exception)
async def universal_fallback_handler(request: Request, exc: Exception):
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred. Please try again later.",
    )


# ── Health & root ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health/system")
async def system_health_check():
    """Checks operational status of Redis, Go Scraper, Celery Workers, and Beat."""
    status_report = {
        "fastapi": "ok",
        "redis": "unknown",
        "go_scraper": "unknown",
        "celery_workers": "unknown",
        "celery_beat": "unknown",
    }

    # 1. Check Redis Connection
    try:
        redis_url = getattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/0")
        redis_client = aioredis.from_url(redis_url, socket_timeout=2.0)
        if await redis_client.ping():
            status_report["redis"] = "ok (reachable)"
        await redis_client.aclose()
    except Exception as e:
        status_report["redis"] = f"error: {str(e)}"

    # 2. Check Go Scraper (internal port 8081)
    try:
        scraper_url = getattr(settings, "GO_SCRAPER_URL", "http://127.0.0.1:8081")
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
            res = await client.get(f"{scraper_url}/health")
            if res.status_code == 200:
                status_report["go_scraper"] = "ok (reachable)"
            else:
                status_report["go_scraper"] = f"unexpected status: {res.status_code}"
    except Exception as e:
        status_report["go_scraper"] = f"unreachable: {str(e)}"

    # 3. Check Celery Worker Ping
    try:
        inspect = celery.control.inspect(timeout=2.0)
        active_workers = inspect.ping()
        if active_workers:
            status_report["celery_workers"] = f"ok ({len(active_workers)} active worker node(s))"
        else:
            status_report["celery_workers"] = "error: no active workers responded"
    except Exception as e:
        status_report["celery_workers"] = f"error: {str(e)}"

    # 4. Check Celery Beat Schedule Responsiveness
    try:
        inspect = celery.control.inspect(timeout=2.0)
        scheduled_tasks = inspect.scheduled()
        if scheduled_tasks is not None:
            status_report["celery_beat"] = "ok (responsive)"
        else:
            status_report["celery_beat"] = "warning: no scheduled tasks active"
    except Exception as e:
        status_report["celery_beat"] = f"error: {str(e)}"

    return status_report


@app.get("/")
def root():
    return Response("Okay")


# ── Register routers ─────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(project_router)
app.include_router(keyword_router)
app.include_router(sources_router)
app.include_router(scraper_router)
app.include_router(ingestion_router)
app.include_router(matching_router)
app.include_router(post_router)
app.include_router(lead_router)