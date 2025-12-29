"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
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


# Custom exception handlers with CORS headers
@app.exception_handler(ReelInException)
async def reelin_exception_handler(request: Request, exc: ReelInException) -> JSONResponse:
    """Handle all ReelIn custom exceptions with proper CORS headers."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "details": exc.details,
            "status_code": exc.status_code,
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global handler for unexpected errors - ensures CORS headers are present."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Capture exception in Sentry
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "details": {"message": str(exc) if settings.debug else "An unexpected error occurred"},
            "status_code": 500,
        },
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
        },
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


