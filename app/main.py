"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import get_settings
from app.core.rate_limit import limiter
from app.core.exceptions import ReelInException
from app.database import init_db
from app.utils.errors import (
    ErrorCode,
    format_error_response,
    get_error_code_for_exception,
)

logger = logging.getLogger(__name__)
from app.api.v1 import router as api_v1_router
from app.api.admin import router as admin_router

settings = get_settings()

# Initialize Sentry for error monitoring
sentry_sdk.init(
    dsn="https://0ea62bf0923d361649015dfab40dcac0@o4507476114800640.ingest.de.sentry.io/4510609416192080",
    send_default_pii=True,
    traces_sample_rate=0.1,
    profiles_sample_rate=0.1,
    environment="production",
    release=f"reelin-backend@{settings.app_version}",
    integrations=[
        FastApiIntegration(),
        SqlalchemyIntegration(),
    ],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Import here to avoid circular imports
    from app.api.v1.live import start_redis_listener, stop_redis_listener

    # Startup
    if settings.debug:
        # In development, create tables automatically
        await init_db()

    # Start Redis Pub/Sub listener for SSE bridge (Celery -> FastAPI)
    await start_redis_listener()

    # Initialize fish classifier model (if available)
    try:
        from app.services.fish_classifier_service import init_fish_classifier
        init_fish_classifier()
    except Exception as e:
        logger.warning(f"Fish classifier initialization skipped: {e}")

    yield

    # Shutdown
    await stop_redis_listener()


app = FastAPI(
    title="ReelIn API",
    description="Fishing Tournament Management Platform API",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# CORS headers for error responses
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}


# Custom exception handlers with CORS headers
@app.exception_handler(ReelInException)
async def reelin_exception_handler(request: Request, exc: ReelInException) -> JSONResponse:
    """Handle all ReelIn custom exceptions with standardized format and CORS headers."""
    # Get error code based on exception type
    error_code = get_error_code_for_exception(exc.__class__.__name__)

    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(
            code=error_code,
            message=exc.message,
            details=exc.details if exc.details else None,
        ),
        headers=CORS_HEADERS,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with standardized format."""
    # Transform Pydantic errors into field-level errors
    field_errors: dict[str, str] = {}
    for error in exc.errors():
        # Build field path from location (skip 'body' prefix)
        loc = error.get("loc", ())
        field_parts = [str(part) for part in loc if part != "body"]
        field = ".".join(field_parts) if field_parts else "request"
        field_errors[field] = error.get("msg", "Invalid value")

    return JSONResponse(
        status_code=422,
        content=format_error_response(
            code=ErrorCode.VALIDATION_ERROR,
            message="error.validation.request_invalid",
            details={"fields": field_errors},
        ),
        headers=CORS_HEADERS,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTPException with standardized format."""
    # Map HTTP status codes to error codes
    status_to_code = {
        400: ErrorCode.VALIDATION_ERROR,
        401: ErrorCode.AUTHENTICATION_ERROR,
        403: ErrorCode.AUTHORIZATION_ERROR,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMIT_ERROR,
    }
    error_code = status_to_code.get(exc.status_code, ErrorCode.INTERNAL_ERROR)

    # Handle dict details (e.g., account_pending_deletion) - return as-is for structured errors
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
            headers=CORS_HEADERS,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(
            code=error_code,
            message=str(exc.detail) if exc.detail else "error.unknown",
            details=None,
        ),
        headers=CORS_HEADERS,
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global handler for unexpected errors - ensures CORS headers are present."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Capture exception in Sentry
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=500,
        content=format_error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="error.internal.unexpected",
            details={"message": str(exc)} if settings.debug else None,
        ),
        headers=CORS_HEADERS,
    )


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session middleware (required for OAuth)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Include API routers
app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/admin")

# Mount uploads directory for serving static files
uploads_dir = Path("uploads")
uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "ReelIn API v2.0",
        "docs": "/docs" if settings.debug else "Disabled in production",
    }


@app.get("/sentry-debug")
async def trigger_error():
    """
    Sentry verification endpoint - triggers a test error.
    Only available in debug mode.

    Usage:
    1. Visit http://localhost:8000/sentry-debug
    2. Check Sentry dashboard for the error event
    """
    if not settings.debug:
        return {"error": "This endpoint is only available in debug mode"}

    # This will trigger a ZeroDivisionError that Sentry should capture
    division_by_zero = 1 / 0
    return {"unreachable": True}


