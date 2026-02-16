"""Admin action logging endpoints."""

import math
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.admin import AdminActionLog
from app.models.user import UserAccount

router = APIRouter()


@router.get("")
async def list_admin_actions(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action_type: Optional[str] = None,
    admin_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
):
    """List admin action logs with filters. Admin only."""
    base = select(AdminActionLog).options(
        selectinload(AdminActionLog.admin),
        selectinload(AdminActionLog.target_user),
    )
    count_q = select(func.count(AdminActionLog.id))

    if action_type:
        base = base.where(AdminActionLog.action_type == action_type)
        count_q = count_q.where(AdminActionLog.action_type == action_type)
    if admin_id:
        base = base.where(AdminActionLog.admin_id == admin_id)
        count_q = count_q.where(AdminActionLog.admin_id == admin_id)
    if target_user_id:
        base = base.where(AdminActionLog.target_user_id == target_user_id)
        count_q = count_q.where(AdminActionLog.target_user_id == target_user_id)

    total = await db.scalar(count_q) or 0
    total_pages = max(1, math.ceil(total / page_size))

    result = await db.execute(
        base.order_by(desc(AdminActionLog.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    items = []
    for log in logs:
        admin_name = None
        if log.admin and log.admin.profile:
            admin_name = f"{log.admin.profile.first_name} {log.admin.profile.last_name}"
        target_name = None
        if log.target_user and log.target_user.profile:
            target_name = f"{log.target_user.profile.first_name} {log.target_user.profile.last_name}"

        items.append({
            "id": log.id,
            "admin_id": log.admin_id,
            "admin_email": log.admin.email if log.admin else None,
            "admin_name": admin_name,
            "action_type": log.action_type,
            "target_user_id": log.target_user_id,
            "target_user_email": log.target_user.email if log.target_user else None,
            "target_user_name": target_name,
            "target_event_id": log.target_event_id,
            "details": log.details,
            "created_at": log.created_at.isoformat(),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
