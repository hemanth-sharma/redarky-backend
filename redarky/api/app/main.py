"""
app/main.py

Redarky API v2 — Social Listening Lead Generation Platform.

Includes all routers from the 9 domains:
  auth, projects, keywords, sources, scraper, ingestion, matching, posts, leads
"""
import os
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

from app.utils.exceptions import (
    NotFoundException, UnauthorizedException, DomainException, ConflictException,
)
from app.config import settings


app = FastAPI(
    title="Redarky API",
    description="Social Listening Lead Generation Platform Backend — v2",
    version="2.0.0",
)

# CORS Middleware Configuration # Basic CORS 
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.ALLOWED_ORIGINS],
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
    # In production: log full stack trace here.
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred. Please try again later.",
    )


# ── Health & root ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


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
