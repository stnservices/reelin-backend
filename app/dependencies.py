"""Shared FastAPI dependencies."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.security import decode_token
from app.models.user import UserAccount, UserProfile, TokenBlacklist
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme
security = HTTPBearer(auto_error=False)

# Cache TTLs
_BLACKLIST_VALID_TTL = 60       # "not blacklisted" cached 60s (max logout delay)
_BLACKLIST_REVOKED_TTL = 300    # "blacklisted" cached 5 min (won't un-blacklist)
_USER_STATUS_TTL = 30           # user active status cached 30s (max ban delay)


# =============================================================================
# Redis-Cached Auth Helpers
# =============================================================================

async def _is_token_blacklisted_cached(token_jti: str, db: AsyncSession) -> bool:
    """Check token blacklist with Redis cache. Returns True if blacklisted."""
    cache_key = f"auth:bl:{token_jti}"
    try:
        cached = await redis_cache.get(cache_key)
        if cached is not None:
            return cached == 1
    except Exception:
        pass  # Redis down — fall through to DB

    # Cache miss — check DB
    result = await db.execute(
        select(TokenBlacklist.id).where(TokenBlacklist.token_jti == token_jti)
    )
    is_blacklisted = result.scalar_one_or_none() is not None

    try:
        ttl = _BLACKLIST_REVOKED_TTL if is_blacklisted else _BLACKLIST_VALID_TTL
        await redis_cache.set(cache_key, 1 if is_blacklisted else 0, ttl=ttl)
    except Exception:
        pass  # Redis down — still works, just no caching

    return is_blacklisted


async def _get_user_status_cached(user_id: int, db: AsyncSession) -> dict | None:
    """Get user active status with Redis cache. Returns dict or None if not found."""
    cache_key = f"auth:u:{user_id}"
    try:
        cached = await redis_cache.get(cache_key)
        if cached is not None:
            return cached
    except Exception:
        pass  # Redis down — fall through to DB

    # Cache miss — minimal DB query (just status fields, no profile/relationships)
    result = await db.execute(
        select(UserAccount.id, UserAccount.is_active, UserAccount.deletion_scheduled_at)
        .where(UserAccount.id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        return None

    status_data = {
        "is_active": row.is_active,
        "has_deletion": row.deletion_scheduled_at is not None,
    }
    try:
        await redis_cache.set(cache_key, status_data, ttl=_USER_STATUS_TTL)
    except Exception:
        pass

    return status_data


async def invalidate_user_auth_cache(user_id: int):
    """Invalidate cached user status. Call after ban/unban/deletion/recovery."""
    try:
        await redis_cache.delete(f"auth:u:{user_id}")
    except Exception:
        pass


async def invalidate_token_cache(token_jti: str):
    """Mark token as blacklisted in cache. Call after logout."""
    try:
        await redis_cache.set(f"auth:bl:{token_jti}", 1, ttl=_BLACKLIST_REVOKED_TTL)
    except Exception:
        pass


# =============================================================================
# JWT Decode Helper (shared logic)
# =============================================================================

def _decode_access_token(token: str) -> tuple[int, str]:
    """Decode JWT and extract (user_id, jti). Raises HTTPException on failure."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
        user_id_str: str = payload.get("sub")
        token_type: str = payload.get("type")
        token_jti: str = payload.get("jti")

        if user_id_str is None:
            raise credentials_exception

        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            raise credentials_exception

        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user_id, token_jti

    except JWTError:
        raise credentials_exception


# =============================================================================
# Auth Dependencies
# =============================================================================

async def get_current_user_id_cached(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> int:
    """
    Lightweight cached auth — returns user_id only, 0 DB queries on cache hit.

    Use for hot polling endpoints that only need user identity (e.g., /my-matches).
    Blacklist check + user status check are Redis-cached.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id, token_jti = _decode_access_token(credentials.credentials)

    # Cached blacklist check
    if await _is_token_blacklisted_cached(token_jti, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Cached user status check
    user_status = await _get_user_status_cached(user_id, db)
    if user_status is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_status["is_active"] and not user_status["has_deletion"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserAccount:
    """
    Get the current authenticated user from JWT token.

    Returns UserAccount with profile loaded. Blacklist check is Redis-cached
    (1 DB query instead of 2).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise credentials_exception

    user_id, token_jti = _decode_access_token(credentials.credentials)

    # CACHED blacklist check (was: DB query every time)
    if await _is_token_blacklisted_cached(token_jti, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user with profile (still DB — needed for ORM object downstream)
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # Allow pending deletion accounts to authenticate (so they can recover)
    # Only reject permanently deactivated accounts
    if not user.is_active and not user.deletion_scheduled_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[UserAccount]:
    """
    Get the current user if authenticated, otherwise return None.
    Useful for endpoints that work for both authenticated and anonymous users.
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


async def get_current_active_user(
    current_user: UserAccount = Depends(get_current_user),
) -> UserAccount:
    """
    Get current user and verify they are active.
    Additional check on top of get_current_user.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )
    return current_user


async def get_current_verified_user(
    current_user: UserAccount = Depends(get_current_user),
) -> UserAccount:
    """
    Get current user and verify their email is verified.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required",
        )
    return current_user
