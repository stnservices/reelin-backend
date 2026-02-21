"""Admin audit & security endpoints."""

import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, func, select, or_, case, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.audit import AuditLog, UserDevice, UserSuspiciousFlag
from app.models.user import UserAccount, UserProfile
from app.schemas.audit import (
    AuditLogResponse,
    AuditStatsResponse,
    BanUserRequest,
    EnrichedAuditLogResponse,
    PaginatedAuditLogs,
    PaginatedEnrichedAuditLogs,
    PaginatedSuspiciousFlags,
    SuspiciousFlagResolve,
    SuspiciousFlagResponse,
    UnbanUserRequest,
    UserDeviceResponse,
)
from app.services.audit_service import log_event, normalize_email

router = APIRouter()


# ── Helpers ──

def _extract_enrichment(log: AuditLog) -> dict:
    """Extract flattened enrichment fields from details JSONB."""
    d = log.details or {}
    parsed_ua = d.get("parsed_ua") or {}
    enrichment = d.get("enrichment") or {}
    return {
        "browser_name": parsed_ua.get("browser_name"),
        "browser_version": parsed_ua.get("browser_version"),
        "os_name": parsed_ua.get("os_name"),
        "os_version": parsed_ua.get("os_version"),
        "device_type": parsed_ua.get("device_type"),
        "country": enrichment.get("country"),
        "country_code": enrichment.get("country_code"),
        "city": enrichment.get("city"),
        "region": enrichment.get("region"),
        "isp": enrichment.get("isp"),
        "is_vpn": enrichment.get("is_vpn"),
        "success": d.get("success"),
        "risk_reasons": d.get("risk_reasons"),
    }


def _build_audit_log_response(log: AuditLog) -> dict:
    """Build response dict from an AuditLog row with joined user data."""
    user_email = log._user_email if hasattr(log, "_user_email") else None
    if not user_email and log.details:
        user_email = log.details.get("attempted_email")
    return {
        "id": log.id,
        "user_id": log.user_id,
        "user_email": user_email,
        "user_name": log._user_name if hasattr(log, "_user_name") else None,
        "event_type": log.event_type,
        "risk_level": log.risk_level,
        "ip_address": str(log.ip_address) if log.ip_address else None,
        "user_agent": log.user_agent,
        "device_id": log.device_id,
        "details": log.details,
        "created_at": log.created_at,
        **_extract_enrichment(log),
    }


def _build_flag_response(flag: UserSuspiciousFlag) -> dict:
    """Build response dict from a flag row with joined user data."""
    return {
        "id": flag.id,
        "flagged_user_id": flag.flagged_user_id,
        "flagged_user_email": flag.flagged_user.email if flag.flagged_user else None,
        "flagged_user_name": (
            f"{flag.flagged_user.profile.first_name} {flag.flagged_user.profile.last_name}"
            if flag.flagged_user and flag.flagged_user.profile else None
        ),
        "matched_banned_user_id": flag.matched_banned_user_id,
        "matched_banned_user_email": flag.matched_banned_user.email if flag.matched_banned_user else None,
        "matched_banned_user_name": (
            f"{flag.matched_banned_user.profile.first_name} {flag.matched_banned_user.profile.last_name}"
            if flag.matched_banned_user and flag.matched_banned_user.profile else None
        ),
        "match_types": flag.match_types,
        "match_details": flag.match_details,
        "risk_score": flag.risk_score,
        "status": flag.status,
        "resolved_by_id": flag.resolved_by_id,
        "resolved_at": flag.resolved_at,
        "resolution_note": flag.resolution_note,
        "created_at": flag.created_at,
    }


# ── Stats ──

@router.get("/stats", response_model=AuditStatsResponse)
async def get_audit_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Dashboard summary stats."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Events today
    events_today = await db.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.created_at >= today_start)
    ) or 0

    # Registrations today
    registrations_today = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= today_start,
            AuditLog.event_type == "registration",
        )
    ) or 0

    # Logins today
    logins_today = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= today_start,
            AuditLog.event_type == "login",
        )
    ) or 0

    # Failed logins today
    failed_logins_today = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.created_at >= today_start,
            AuditLog.event_type == "login_failed",
        )
    ) or 0

    # Pending flags
    pending_flags = await db.scalar(
        select(func.count(UserSuspiciousFlag.id)).where(
            UserSuspiciousFlag.status == "pending"
        )
    ) or 0

    # Banned users total
    banned_users = await db.scalar(
        select(func.count(UserAccount.id)).where(UserAccount.is_banned == True)
    ) or 0

    return AuditStatsResponse(
        events_today=events_today,
        registrations_today=registrations_today,
        logins_today=logins_today,
        failed_logins_today=failed_logins_today,
        pending_flags=pending_flags,
        banned_users=banned_users,
    )


# ── Audit Logs ──

@router.get("/logs", response_model=PaginatedEnrichedAuditLogs)
async def list_audit_logs(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    """Paginated audit logs with filters."""
    base = select(AuditLog)
    count_q = select(func.count(AuditLog.id))

    if event_type:
        base = base.where(AuditLog.event_type == event_type)
        count_q = count_q.where(AuditLog.event_type == event_type)
    if risk_level:
        base = base.where(AuditLog.risk_level == risk_level)
        count_q = count_q.where(AuditLog.risk_level == risk_level)
    if user_id:
        base = base.where(AuditLog.user_id == user_id)
        count_q = count_q.where(AuditLog.user_id == user_id)
    if date_from:
        base = base.where(AuditLog.created_at >= date_from)
        count_q = count_q.where(AuditLog.created_at >= date_from)
    if date_to:
        base = base.where(AuditLog.created_at <= date_to)
        count_q = count_q.where(AuditLog.created_at <= date_to)
    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(
                AuditLog.ip_address.cast(String).ilike(pattern) if AuditLog.ip_address is not None else False,
                AuditLog.device_id.ilike(pattern),
            )
        )
        count_q = count_q.where(
            or_(
                AuditLog.ip_address.cast(String).ilike(pattern) if AuditLog.ip_address is not None else False,
                AuditLog.device_id.ilike(pattern),
            )
        )

    total = await db.scalar(count_q) or 0
    total_pages = max(1, math.ceil(total / page_size))

    logs_result = await db.execute(
        base.order_by(desc(AuditLog.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = logs_result.scalars().all()

    # Batch load user info
    user_ids = {l.user_id for l in logs if l.user_id}
    user_map = {}
    if user_ids:
        users_result = await db.execute(
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.id.in_(user_ids))
        )
        for u in users_result.scalars().all():
            name = f"{u.profile.first_name} {u.profile.last_name}" if u.profile else None
            user_map[u.id] = {"email": u.email, "name": name}

    items = []
    for log in logs:
        info = user_map.get(log.user_id, {})
        email = info.get("email")
        if not email and log.details:
            email = log.details.get("attempted_email")
        items.append(EnrichedAuditLogResponse(
            id=log.id,
            user_id=log.user_id,
            user_email=email,
            user_name=info.get("name"),
            event_type=log.event_type,
            risk_level=log.risk_level,
            ip_address=str(log.ip_address) if log.ip_address else None,
            user_agent=log.user_agent,
            device_id=log.device_id,
            details=log.details,
            created_at=log.created_at,
            **_extract_enrichment(log),
        ))

    return PaginatedEnrichedAuditLogs(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ── Flagged Users ──

@router.get("/flagged-users", response_model=PaginatedSuspiciousFlags)
async def list_flagged_users(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    flag_status: Optional[str] = Query(None, alias="status"),
    min_risk_score: Optional[int] = None,
):
    """Paginated suspicious flags."""
    base = (
        select(UserSuspiciousFlag)
        .options(
            selectinload(UserSuspiciousFlag.flagged_user).selectinload(UserAccount.profile),
            selectinload(UserSuspiciousFlag.matched_banned_user).selectinload(UserAccount.profile),
        )
    )
    count_q = select(func.count(UserSuspiciousFlag.id))

    if flag_status:
        base = base.where(UserSuspiciousFlag.status == flag_status)
        count_q = count_q.where(UserSuspiciousFlag.status == flag_status)
    if min_risk_score is not None:
        base = base.where(UserSuspiciousFlag.risk_score >= min_risk_score)
        count_q = count_q.where(UserSuspiciousFlag.risk_score >= min_risk_score)

    total = await db.scalar(count_q) or 0
    total_pages = max(1, math.ceil(total / page_size))

    result = await db.execute(
        base.order_by(desc(UserSuspiciousFlag.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    flags = result.scalars().all()

    items = [SuspiciousFlagResponse(**_build_flag_response(f)) for f in flags]

    return PaginatedSuspiciousFlags(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.patch("/flagged-users/{flag_id}", response_model=SuspiciousFlagResponse)
async def resolve_flag(
    flag_id: int,
    data: SuspiciousFlagResolve,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Confirm or dismiss a suspicious flag."""
    result = await db.execute(
        select(UserSuspiciousFlag)
        .options(
            selectinload(UserSuspiciousFlag.flagged_user).selectinload(UserAccount.profile),
            selectinload(UserSuspiciousFlag.matched_banned_user).selectinload(UserAccount.profile),
        )
        .where(UserSuspiciousFlag.id == flag_id)
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")

    flag.status = data.status
    flag.resolved_by_id = current_user.id
    flag.resolved_at = datetime.now(timezone.utc)
    flag.resolution_note = data.resolution_note
    await db.commit()
    await db.refresh(flag)

    return SuspiciousFlagResponse(**_build_flag_response(flag))


# ── User Devices ──

@router.get("/users/{user_id}/devices", response_model=list[UserDeviceResponse])
async def get_user_devices(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Device history for a user."""
    result = await db.execute(
        select(UserDevice)
        .where(UserDevice.user_id == user_id)
        .order_by(desc(UserDevice.last_seen_at))
    )
    devices = result.scalars().all()
    return [
        UserDeviceResponse(
            id=d.id,
            user_id=d.user_id,
            device_id=d.device_id,
            device_name=d.device_name,
            os=d.os,
            os_version=d.os_version,
            brand=d.brand,
            model=d.model,
            first_seen_at=d.first_seen_at,
            last_seen_at=d.last_seen_at,
            first_ip=str(d.first_ip) if d.first_ip else None,
            last_ip=str(d.last_ip) if d.last_ip else None,
        )
        for d in devices
    ]


# ── User Timeline ──

@router.get("/users/{user_id}/timeline")
async def get_user_timeline(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Combined audit log timeline for a user."""
    count_q = select(func.count(AuditLog.id)).where(AuditLog.user_id == user_id)
    total = await db.scalar(count_q) or 0
    total_pages = max(1, math.ceil(total / page_size))

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == user_id)
        .order_by(desc(AuditLog.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    items = [
        EnrichedAuditLogResponse(
            id=log.id,
            user_id=log.user_id,
            event_type=log.event_type,
            risk_level=log.risk_level,
            ip_address=str(log.ip_address) if log.ip_address else None,
            user_agent=log.user_agent,
            device_id=log.device_id,
            details=log.details,
            created_at=log.created_at,
            **_extract_enrichment(log),
        )
        for log in logs
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# ── Ban / Unban ──

@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    data: BanUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Ban a user."""
    result = await db.execute(select(UserAccount).where(UserAccount.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_banned:
        raise HTTPException(status_code=400, detail="User is already banned")

    user.is_banned = True
    user.banned_at = datetime.now(timezone.utc)
    user.ban_reason = data.reason
    user.is_active = False

    log_event(
        db,
        event_type="ban",
        user_id=user_id,
        details={"reason": data.reason, "admin_id": current_user.id},
    )

    await db.commit()
    return {"message": "User banned", "user_id": user_id}


@router.post("/users/{user_id}/unban")
async def unban_user(
    user_id: int,
    data: UnbanUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Unban a user and reactivate their account."""
    result = await db.execute(select(UserAccount).where(UserAccount.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_banned:
        raise HTTPException(status_code=400, detail="User is not banned")

    user.is_banned = False
    user.banned_at = None
    user.ban_reason = None
    user.is_active = True

    log_event(
        db,
        event_type="unban",
        user_id=user_id,
        details={"reason": data.reason, "admin_id": current_user.id},
    )

    await db.commit()
    return {"message": "User unbanned and reactivated", "user_id": user_id}
