"""Admin endpoints for managing organizer permissions."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount, UserProfile
from app.models.organizer_permissions import OrganizerEventTypeAccess, NationalEventOrganizer
from app.models.event import EventType
from app.schemas.organizer_permissions import (
    EventTypeAccessCreate,
    EventTypeAccessBulkCreate,
    EventTypeAccessResponse,
    EventTypeAccessListResponse,
    NationalOrganizerCreate,
    NationalOrganizerResponse,
    NationalOrganizerListResponse,
    OrganizerPermissionSummary,
    OrganizerSearchResult,
    OrganizerSearchResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Organizer Search Endpoint
# ============================================================================


@router.get("/organizers/search", response_model=OrganizerSearchResponse)
async def search_organizers(
    q: str = Query(..., min_length=2, description="Search query (name or email)"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Search for users with organizer role by name or email.

    Admin only endpoint. Used for granting permissions.
    """
    from sqlalchemy import or_, text

    search_pattern = f"%{q.lower()}%"

    # Build query - search by email or name, filter for organizer role
    query = (
        select(UserAccount)
        .join(UserProfile, UserAccount.id == UserProfile.user_id)
        .options(selectinload(UserAccount.profile))
        .where(
            UserAccount.is_active == True,
            # Check if 'organizer' is in the roles JSONB array
            text("user_profiles.roles::jsonb ? 'organizer'"),
            or_(
                func.lower(UserAccount.email).ilike(search_pattern),
                func.lower(UserProfile.first_name).ilike(search_pattern),
                func.lower(UserProfile.last_name).ilike(search_pattern),
                func.lower(func.concat(UserProfile.first_name, ' ', UserProfile.last_name)).ilike(search_pattern),
            )
        )
        .limit(limit)
        .order_by(UserProfile.first_name, UserProfile.last_name)
    )

    result = await db.execute(query)
    users = result.scalars().all()

    items = []
    for user in users:
        if user.profile:
            items.append({
                "id": user.id,
                "email": user.email,
                "first_name": user.profile.first_name,
                "last_name": user.profile.last_name,
                "full_name": user.profile.full_name,
                "avatar_url": user.profile.profile_picture_url,
            })

    return {
        "items": items,
        "total": len(items),
    }


def _build_event_type_access_response(access: OrganizerEventTypeAccess) -> dict:
    """Build response dict from access record with user/event type details."""
    return {
        "id": access.id,
        "user_id": access.user_id,
        "user_name": access.user.profile.full_name if access.user and access.user.profile else None,
        "user_email": access.user.email if access.user else None,
        "event_type_id": access.event_type_id,
        "event_type_name": access.event_type.name if access.event_type else None,
        "granted_by_id": access.granted_by_id,
        "granted_by_name": access.granted_by.profile.full_name if access.granted_by and access.granted_by.profile else None,
        "granted_at": access.granted_at,
        "notes": access.notes,
        "is_active": access.is_active,
    }


def _build_national_organizer_response(organizer: NationalEventOrganizer) -> dict:
    """Build response dict from national organizer record with user details."""
    return {
        "id": organizer.id,
        "user_id": organizer.user_id,
        "user_name": organizer.user.profile.full_name if organizer.user and organizer.user.profile else None,
        "user_email": organizer.user.email if organizer.user else None,
        "granted_by_id": organizer.granted_by_id,
        "granted_by_name": organizer.granted_by.profile.full_name if organizer.granted_by and organizer.granted_by.profile else None,
        "granted_at": organizer.granted_at,
        "reason": organizer.reason,
        "is_active": organizer.is_active,
    }


# ============================================================================
# Event Type Access Endpoints
# ============================================================================


@router.get("/event-type", response_model=EventTypeAccessListResponse)
async def list_event_type_access(
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    event_type_id: Optional[int] = Query(None, description="Filter by event type ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    List all event type access grants with optional filters.

    Admin only endpoint.
    """
    query = (
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.user).selectinload(UserAccount.profile),
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.granted_by).selectinload(UserAccount.profile),
        )
    )

    if user_id is not None:
        query = query.where(OrganizerEventTypeAccess.user_id == user_id)
    if event_type_id is not None:
        query = query.where(OrganizerEventTypeAccess.event_type_id == event_type_id)
    if is_active is not None:
        query = query.where(OrganizerEventTypeAccess.is_active == is_active)

    query = query.offset(skip).limit(limit).order_by(OrganizerEventTypeAccess.granted_at.desc())
    result = await db.execute(query)
    items = result.scalars().all()

    # Get total count
    count_query = select(func.count(OrganizerEventTypeAccess.id))
    if user_id is not None:
        count_query = count_query.where(OrganizerEventTypeAccess.user_id == user_id)
    if event_type_id is not None:
        count_query = count_query.where(OrganizerEventTypeAccess.event_type_id == event_type_id)
    if is_active is not None:
        count_query = count_query.where(OrganizerEventTypeAccess.is_active == is_active)
    total = await db.scalar(count_query)

    return {
        "items": [_build_event_type_access_response(item) for item in items],
        "total": total or 0,
    }


@router.post("/event-type", response_model=EventTypeAccessResponse)
async def grant_event_type_access(
    data: EventTypeAccessCreate,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Grant event type access to an organizer.

    Admin only endpoint.
    """
    # Verify user exists
    user = await db.get(UserAccount, data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify event type exists
    event_type = await db.get(EventType, data.event_type_id)
    if not event_type:
        raise HTTPException(status_code=404, detail="Event type not found")

    # Check if already exists
    existing = await db.execute(
        select(OrganizerEventTypeAccess).where(
            OrganizerEventTypeAccess.user_id == data.user_id,
            OrganizerEventTypeAccess.event_type_id == data.event_type_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already has access to this event type")

    access = OrganizerEventTypeAccess(
        user_id=data.user_id,
        event_type_id=data.event_type_id,
        granted_by_id=admin.id,
        notes=data.notes,
    )
    db.add(access)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.user).selectinload(UserAccount.profile),
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.granted_by).selectinload(UserAccount.profile),
        )
        .where(OrganizerEventTypeAccess.id == access.id)
    )
    access = result.scalar_one()

    logger.info(f"Admin {admin.id} granted user {data.user_id} access to event type {data.event_type_id}")

    return _build_event_type_access_response(access)


@router.post("/event-type/bulk", response_model=list)
async def grant_event_type_access_bulk(
    data: EventTypeAccessBulkCreate,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Grant multiple event types to a user at once.

    Skips any event types the user already has access to.
    Admin only endpoint.
    """
    # Verify user exists
    user = await db.get(UserAccount, data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get existing access
    existing_result = await db.execute(
        select(OrganizerEventTypeAccess.event_type_id).where(
            OrganizerEventTypeAccess.user_id == data.user_id,
            OrganizerEventTypeAccess.event_type_id.in_(data.event_type_ids),
        )
    )
    existing_ids = set(existing_result.scalars().all())

    created_ids = []
    for event_type_id in data.event_type_ids:
        if event_type_id in existing_ids:
            continue

        # Verify event type exists
        event_type = await db.get(EventType, event_type_id)
        if not event_type:
            continue

        access = OrganizerEventTypeAccess(
            user_id=data.user_id,
            event_type_id=event_type_id,
            granted_by_id=admin.id,
            notes=data.notes,
        )
        db.add(access)
        created_ids.append(access)

    await db.commit()

    # Reload with relationships
    if created_ids:
        result = await db.execute(
            select(OrganizerEventTypeAccess)
            .options(
                selectinload(OrganizerEventTypeAccess.user).selectinload(UserAccount.profile),
                selectinload(OrganizerEventTypeAccess.event_type),
                selectinload(OrganizerEventTypeAccess.granted_by).selectinload(UserAccount.profile),
            )
            .where(OrganizerEventTypeAccess.user_id == data.user_id)
            .where(OrganizerEventTypeAccess.event_type_id.in_(data.event_type_ids))
            .where(OrganizerEventTypeAccess.event_type_id.notin_(existing_ids))
        )
        created = result.scalars().all()
        logger.info(f"Admin {admin.id} granted user {data.user_id} bulk access to {len(created)} event types")
        return [_build_event_type_access_response(item) for item in created]

    return []


@router.delete("/event-type/{access_id}")
async def revoke_event_type_access(
    access_id: int,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Revoke event type access by ID.

    Admin only endpoint.
    """
    access = await db.get(OrganizerEventTypeAccess, access_id)
    if not access:
        raise HTTPException(status_code=404, detail="Access record not found")

    user_id = access.user_id
    event_type_id = access.event_type_id

    await db.delete(access)
    await db.commit()

    logger.info(f"Admin {admin.id} revoked user {user_id} access to event type {event_type_id}")

    return {"message": "Access revoked"}


@router.get("/event-type/by-user/{user_id}", response_model=list)
async def get_user_event_type_access(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Get all event type access for a specific user.

    Admin only endpoint.
    """
    result = await db.execute(
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.user).selectinload(UserAccount.profile),
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.granted_by).selectinload(UserAccount.profile),
        )
        .where(OrganizerEventTypeAccess.user_id == user_id)
        .order_by(OrganizerEventTypeAccess.event_type_id)
    )
    items = result.scalars().all()
    return [_build_event_type_access_response(item) for item in items]


@router.delete("/event-type/by-user/{user_id}")
async def revoke_all_user_access(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Revoke all event type access from a user.

    Admin only endpoint.
    """
    result = await db.execute(
        delete(OrganizerEventTypeAccess).where(OrganizerEventTypeAccess.user_id == user_id)
    )
    count = result.rowcount
    await db.commit()

    logger.info(f"Admin {admin.id} revoked all event type access from user {user_id} ({count} records)")

    return {"message": f"Revoked {count} access records for user"}


# ============================================================================
# National Event Organizers Endpoints
# ============================================================================


@router.get("/national", response_model=NationalOrganizerListResponse)
async def list_national_organizers(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    List all national event organizers.

    Admin only endpoint.
    """
    query = (
        select(NationalEventOrganizer)
        .options(
            selectinload(NationalEventOrganizer.user).selectinload(UserAccount.profile),
            selectinload(NationalEventOrganizer.granted_by).selectinload(UserAccount.profile),
        )
    )

    if is_active is not None:
        query = query.where(NationalEventOrganizer.is_active == is_active)

    query = query.offset(skip).limit(limit).order_by(NationalEventOrganizer.granted_at.desc())
    result = await db.execute(query)
    items = result.scalars().all()

    # Get total count
    count_query = select(func.count(NationalEventOrganizer.id))
    if is_active is not None:
        count_query = count_query.where(NationalEventOrganizer.is_active == is_active)
    total = await db.scalar(count_query)

    return {
        "items": [_build_national_organizer_response(item) for item in items],
        "total": total or 0,
    }


@router.post("/national", response_model=NationalOrganizerResponse)
async def grant_national_permission(
    data: NationalOrganizerCreate,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Grant national event permission to an organizer.

    Admin only endpoint.
    """
    # Verify user exists
    user = await db.get(UserAccount, data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already exists
    existing = await db.execute(
        select(NationalEventOrganizer).where(NationalEventOrganizer.user_id == data.user_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already has national event permission")

    organizer = NationalEventOrganizer(
        user_id=data.user_id,
        granted_by_id=admin.id,
        reason=data.reason,
    )
    db.add(organizer)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(NationalEventOrganizer)
        .options(
            selectinload(NationalEventOrganizer.user).selectinload(UserAccount.profile),
            selectinload(NationalEventOrganizer.granted_by).selectinload(UserAccount.profile),
        )
        .where(NationalEventOrganizer.id == organizer.id)
    )
    organizer = result.scalar_one()

    logger.info(f"Admin {admin.id} granted user {data.user_id} national event permission")

    return _build_national_organizer_response(organizer)


@router.delete("/national/{user_id}")
async def revoke_national_permission(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Revoke national event permission from a user.

    Admin only endpoint.
    """
    result = await db.execute(
        select(NationalEventOrganizer).where(NationalEventOrganizer.user_id == user_id)
    )
    organizer = result.scalar_one_or_none()
    if not organizer:
        raise HTTPException(status_code=404, detail="User does not have national permission")

    await db.delete(organizer)
    await db.commit()

    logger.info(f"Admin {admin.id} revoked national event permission from user {user_id}")

    return {"message": "National permission revoked"}


# ============================================================================
# Summary Endpoint
# ============================================================================


@router.get("/summary/{user_id}", response_model=OrganizerPermissionSummary)
async def get_organizer_permission_summary(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: UserAccount = Depends(AdminOnly),
):
    """
    Get a complete summary of an organizer's permissions.

    Admin only endpoint.
    """
    # Verify user exists
    user_result = await db.execute(
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get event type access
    access_result = await db.execute(
        select(OrganizerEventTypeAccess)
        .options(
            selectinload(OrganizerEventTypeAccess.user).selectinload(UserAccount.profile),
            selectinload(OrganizerEventTypeAccess.event_type),
            selectinload(OrganizerEventTypeAccess.granted_by).selectinload(UserAccount.profile),
        )
        .where(OrganizerEventTypeAccess.user_id == user_id)
        .order_by(OrganizerEventTypeAccess.event_type_id)
    )
    access_items = access_result.scalars().all()

    # Get national permission
    national_result = await db.execute(
        select(NationalEventOrganizer)
        .options(
            selectinload(NationalEventOrganizer.user).selectinload(UserAccount.profile),
            selectinload(NationalEventOrganizer.granted_by).selectinload(UserAccount.profile),
        )
        .where(NationalEventOrganizer.user_id == user_id)
    )
    national = national_result.scalar_one_or_none()

    return {
        "user_id": user_id,
        "user_name": user.profile.full_name if user.profile else user.email,
        "user_email": user.email,
        "event_type_access": [_build_event_type_access_response(item) for item in access_items],
        "can_create_national": national is not None and national.is_active,
        "national_permission": _build_national_organizer_response(national) if national else None,
    }
