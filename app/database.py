"""Database configuration and session management."""

from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Create sync engine for Celery tasks (avoids event loop issues)
# Convert async URL to sync URL (postgresql+asyncpg -> postgresql+psycopg2)
sync_database_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")
sync_engine = create_engine(
    sync_database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=3,  # Max 5 for Celery sync
)

# Create async engine
# Pool sized to fit within DigitalOcean's 22 connection pool limit
# Async: 8, Sync: 5, Celery async: 4 = 17 max (leaves headroom)
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=4,
    max_overflow=4,  # Max 8 for FastAPI async
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
    Pool sized to fit within DigitalOcean's 22 connection limit.
    """
    celery_engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,  # Max 4 for Celery async
    )
    return async_sessionmaker(
        celery_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
