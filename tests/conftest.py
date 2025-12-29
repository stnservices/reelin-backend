"""Pytest fixtures for testing."""

import asyncio
import os
from typing import AsyncGenerator, Generator

# Set TESTING env var BEFORE importing app to disable rate limiting
os.environ["TESTING"] = "1"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app


# Use PostgreSQL for tests (same as production, but can use separate test DB)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://reelin:reelin_dev@db:5432/reelin_test"
)

# Create test engine
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

# Create test session factory
test_session_maker = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    # Create all tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_maker() as session:
        yield session

    # Drop all tables after test (this also cleans up any committed data)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with overridden database dependency."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        # Return a fresh session from the same connection pool
        # This allows commits to be visible across API calls
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def test_user_data() -> dict:
    """Sample user registration data."""
    return {
        "email": "test@example.com",
        "password": "SecurePass123",
        "first_name": "Test",
        "last_name": "User",
    }


@pytest.fixture
async def registered_user(client: AsyncClient, test_user_data: dict) -> dict:
    """Create and return a registered user."""
    response = await client.post("/api/v1/auth/register", json=test_user_data)
    assert response.status_code == 201
    return {**response.json(), "password": test_user_data["password"]}


@pytest.fixture
async def auth_tokens(client: AsyncClient, registered_user: dict) -> dict:
    """Get authentication tokens for a registered user."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def auth_headers(auth_tokens: dict) -> dict:
    """Get authorization headers with access token."""
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}
