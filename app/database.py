"""Database configuration and session management."""

import os
from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Pool configuration (configurable via env vars for tuning without redeploy)
# With DO's PgBouncer in transaction mode, smaller pools are fine
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))

# Sync pool for Celery (smaller)
DB_SYNC_POOL_SIZE = int(os.getenv("DB_SYNC_POOL_SIZE", "2"))
DB_SYNC_MAX_OVERFLOW = int(os.getenv("DB_SYNC_MAX_OVERFLOW", "3"))

# Create sync engine for Celery tasks (avoids event loop issues)
# Convert async URL to sync URL (postgresql+asyncpg -> postgresql+psycopg2)
sync_database_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")
sync_engine = create_engine(
    sync_database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=DB_SYNC_POOL_SIZE,
    max_overflow=DB_SYNC_MAX_OVERFLOW,
)

# Create async engine
# With PgBouncer transaction pooling, SQLAlchemy pool is a secondary buffer
# Connections are quickly returned to PgBouncer after each request
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def create_celery_session_maker():
    """Create a fresh async session maker for Celery tasks.

    This avoids event loop conflicts when Celery creates its own event loop.
    Uses smaller pool since Celery tasks are less frequent.
    """
    celery_async_pool = int(os.getenv("DB_CELERY_POOL_SIZE", "2"))
    celery_async_overflow = int(os.getenv("DB_CELERY_MAX_OVERFLOW", "2"))
    celery_engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=celery_async_pool,
        max_overflow=celery_async_overflow,
    )
    return async_sessionmaker(
        celery_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
