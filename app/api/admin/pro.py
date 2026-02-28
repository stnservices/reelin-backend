"""Admin Pro subscription management endpoints."""

import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount, UserProfile
from app.models.pro import ProGrant, ProAuditLog, ProSettings, GrantType, ProAction
from app.schemas.pro import (
    ProStatsResponse,
    ProGrantCreate,
    ProGrantResponse,
    ProGrantRevoke,
    ProGrantListResponse,
    SubscriptionListItem,
    SubscriptionListResponse,
    SubscriptionDetail,
    SubscriptionExtend,
    SubscriptionCancel,
    RefundCreate,
    UserProStatus,
    UserProStatusListResponse,
    AuditLogEntry,
    AuditLogListResponse,
    ProSettingResponse,
    ProSettingUpdate,
    ProSettingsResponse,
    ProStatusFilter,
    RevenueDataPoint,
    ConversionFunnelData,
)

router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================

async def log_pro_action(
    db: AsyncSession,
    admin_id: int,
    user_id: int,
    action: str,
    reason: Optional[str] = None,
    details: Optional[dict] = None,
    stripe_event_id: Optional[str] = None,
) -> ProAuditLog:
    """Log a Pro-related admin action."""
    log_entry = ProAuditLog(
        admin_id=admin_id,
        user_id=user_id,
        action=action,
        reason=reason,
        details=details,
        stripe_event_id=stripe_event_id,
    )
    db.add(log_entry)
    await db.flush()
    return log_entry


async def get_setting_value(db: AsyncSession, key: str, default: str = "") -> str:
    """Get a Pro setting value."""
    query = select(ProSettings).where(ProSettings.key == key)
    result = await db.execute(query)
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def check_user_has_active_grant(db: AsyncSession, user_id: int) -> Optional[ProGrant]:
    """Check if user has an active manual grant."""
    now = datetime.now(timezone.utc)
    query = select(ProGrant).where(
        and_(
            ProGrant.user_id == user_id,
            ProGrant.is_active == True,
            or_(
                ProGrant.expires_at.is_(None),  # Lifetime
                ProGrant.expires_at > now,  # Not expired
            ),
        )
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


def format_grant_response(grant: ProGrant) -> ProGrantResponse:
    """Format a ProGrant model to response schema."""
    return ProGrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        user_email=grant.user.email if grant.user else "",
        user_name=grant.user.profile.full_name if grant.user and grant.user.profile else "",
        granted_by=grant.granted_by,
        granter_name=grant.granter.profile.full_name if grant.granter and grant.granter.profile else "",
        grant_type=grant.grant_type,
        duration_days=grant.duration_days,
        starts_at=grant.starts_at,
        expires_at=grant.expires_at,
        reason=grant.reason,
        is_active=grant.is_active,
        is_lifetime=grant.is_lifetime,
        revoked_at=grant.revoked_at,
        revoked_by=grant.revoked_by,
        revoker_name=grant.revoker.profile.full_name if grant.revoker and grant.revoker.profile else None,
        revoke_reason=grant.revoke_reason,
        created_at=grant.created_at,
    )


# ============================================================================
# Stats Endpoints
# ============================================================================

@router.get("/stats", response_model=ProStatsResponse)
async def get_pro_stats(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> ProStatsResponse:
    """Get Pro subscription statistics. Admin only."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Total Pro users (Stripe + manual grants)
    total_pro_query = select(func.count()).select_from(UserAccount).where(
        UserAccount.is_pro == True
    )
    total_pro_result = await db.execute(total_pro_query)
    total_pro_users = total_pro_result.scalar() or 0

    # Active Stripe subscriptions
    active_subs_query = select(func.count()).select_from(UserAccount).where(
        and_(
            UserAccount.pro_stripe_subscription_id.isnot(None),
            UserAccount.is_pro == True,
        )
    )
    active_subs_result = await db.execute(active_subs_query)
    active_subscriptions = active_subs_result.scalar() or 0

    # Active manual grants
    manual_grants_query = select(func.count()).select_from(ProGrant).where(
        and_(
            ProGrant.is_active == True,
            or_(
                ProGrant.expires_at.is_(None),
                ProGrant.expires_at > now,
            ),
        )
    )
    manual_grants_result = await db.execute(manual_grants_query)
    manual_grants = manual_grants_result.scalar() or 0

    # Monthly vs Yearly subscribers
    monthly_query = select(func.count()).select_from(UserAccount).where(
        and_(
            UserAccount.pro_plan_type == "monthly",
            UserAccount.is_pro == True,
        )
    )
    monthly_result = await db.execute(monthly_query)
    monthly_subscribers = monthly_result.scalar() or 0

    yearly_query = select(func.count()).select_from(UserAccount).where(
        and_(
            UserAccount.pro_plan_type == "yearly",
            UserAccount.is_pro == True,
        )
    )
    yearly_result = await db.execute(yearly_query)
    yearly_subscribers = yearly_result.scalar() or 0

    # Get prices from settings
    monthly_price = Decimal(await get_setting_value(db, "monthly_price_eur", "2.99"))
    yearly_price = Decimal(await get_setting_value(db, "yearly_price_eur", "19.99"))

    # Calculate MRR and ARR
    mrr = (monthly_subscribers * monthly_price) + (yearly_subscribers * (yearly_price / 12))
    arr = mrr * 12

    # New this month
    new_this_month_query = select(func.count()).select_from(UserAccount).where(
        and_(
            UserAccount.is_pro == True,
            UserAccount.pro_started_at >= month_start,
        )
    )
    new_this_month_result = await db.execute(new_this_month_query)
    new_this_month = new_this_month_result.scalar() or 0

    # Total users for conversion rate
    total_users_query = select(func.count()).select_from(UserAccount).where(
        UserAccount.is_active == True
    )
    total_users_result = await db.execute(total_users_query)
    total_users = total_users_result.scalar() or 1  # Avoid division by zero

    conversion_rate = (total_pro_users / total_users) * 100 if total_users > 0 else 0

    # Churn rate (simplified - users who cancelled this month vs active last month)
    # For now, return 0 as we don't have historical data
    churn_rate = 0.0

    return ProStatsResponse(
        total_pro_users=total_pro_users,
        active_subscriptions=active_subscriptions,
        manual_grants=manual_grants,
        mrr=mrr,
        arr=arr,
        churn_rate=churn_rate,
        new_this_month=new_this_month,
        monthly_subscribers=monthly_subscribers,
        yearly_subscribers=yearly_subscribers,
        conversion_rate=round(conversion_rate, 2),
    )


@router.get("/stats/revenue-chart")
async def get_revenue_chart(
    months: int = Query(12, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[RevenueDataPoint]:
    """Get revenue chart data for the last N months. Admin only."""
    # For now, return empty data as we don't have payment history
    # This would need Stripe integration to populate
    return []


@router.get("/stats/conversion-funnel")
async def get_conversion_funnel(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> ConversionFunnelData:
    """Get conversion funnel data. Admin only."""
    # Total users
    total_query = select(func.count()).select_from(UserAccount).where(
        UserAccount.is_active == True
    )
    total_result = await db.execute(total_query)
    total_users = total_result.scalar() or 0

    # Pro users
    pro_query = select(func.count()).select_from(UserAccount).where(
        and_(
            UserAccount.is_active == True,
            UserAccount.is_pro == True,
        )
    )
    pro_result = await db.execute(pro_query)
    pro_users = pro_result.scalar() or 0

    # Trial users (for future implementation)
    trial_users = 0

    free_users = total_users - pro_users - trial_users

    return ConversionFunnelData(
        total_users=total_users,
        free_users=free_users,
        trial_users=trial_users,
        pro_users=pro_users,
    )


# ============================================================================
# Subscription Endpoints
# ============================================================================

@router.get("/subscriptions", response_model=SubscriptionListResponse)
async def list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None,
    plan_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """List all Pro subscriptions with pagination and filters. Admin only."""
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.is_pro == True)
    )

    # Search by email or name
    if search:
        query = query.join(UserProfile, isouter=True).where(
            or_(
                UserAccount.email.ilike(f"%{search}%"),
                UserProfile.first_name.ilike(f"%{search}%"),
                UserProfile.last_name.ilike(f"%{search}%"),
            )
        )

    # Filter by plan type
    if plan_type:
        query = query.where(UserAccount.pro_plan_type == plan_type)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(UserAccount.pro_started_at.desc().nullsfirst())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    # Batch-load active grants for all users on this page
    user_ids = [u.id for u in users]
    grants_by_user_id = {}
    if user_ids:
        now_ts = datetime.now(timezone.utc)
        grants_query = select(ProGrant).where(
            and_(
                ProGrant.user_id.in_(user_ids),
                ProGrant.is_active == True,
                or_(
                    ProGrant.expires_at.is_(None),
                    ProGrant.expires_at > now_ts,
                ),
            )
        )
        grants_result = await db.execute(grants_query)
        for g in grants_result.scalars().all():
            grants_by_user_id[g.user_id] = g

    # Format response
    items = []
    for user in users:
        grant = grants_by_user_id.get(user.id)

        items.append(SubscriptionListItem(
            id=user.id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.profile.full_name if user.profile else "",
            plan_type=user.pro_plan_type,
            status="active" if user.is_pro else "expired",
            started_at=user.pro_started_at,
            expires_at=user.pro_expires_at,
            amount=None,  # Would come from Stripe
            stripe_subscription_id=user.pro_stripe_subscription_id,
            stripe_customer_id=user.pro_stripe_customer_id,
            is_manual_grant=grant is not None,
            grant_id=grant.id if grant else None,
        ))

    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/subscriptions/{user_id}", response_model=SubscriptionDetail)
async def get_subscription_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> SubscriptionDetail:
    """Get detailed subscription information for a user. Admin only."""
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == user_id)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get manual grants for this user
    grants_query = (
        select(ProGrant)
        .options(
            selectinload(ProGrant.user).selectinload(UserAccount.profile),
            selectinload(ProGrant.granter).selectinload(UserAccount.profile),
            selectinload(ProGrant.revoker).selectinload(UserAccount.profile),
        )
        .where(ProGrant.user_id == user_id)
        .order_by(ProGrant.created_at.desc())
    )
    grants_result = await db.execute(grants_query)
    grants = grants_result.scalars().all()

    active_grant = await check_user_has_active_grant(db, user_id)

    return SubscriptionDetail(
        id=user.id,
        user_id=user.id,
        user_email=user.email,
        user_name=user.profile.full_name if user.profile else "",
        plan_type=user.pro_plan_type,
        status="active" if user.is_pro else "expired",
        started_at=user.pro_started_at,
        expires_at=user.pro_expires_at,
        amount=None,
        stripe_subscription_id=user.pro_stripe_subscription_id,
        stripe_customer_id=user.pro_stripe_customer_id,
        stripe_portal_url=None,  # Would generate from Stripe
        is_manual_grant=active_grant is not None,
        manual_grants=[format_grant_response(g) for g in grants],
        payment_history=[],  # Would come from Stripe
    )


# ============================================================================
# Grant Endpoints
# ============================================================================

@router.get("/grants", response_model=ProGrantListResponse)
async def list_grants(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """List all Pro grants. Admin only."""
    query = (
        select(ProGrant)
        .options(
            selectinload(ProGrant.user).selectinload(UserAccount.profile),
            selectinload(ProGrant.granter).selectinload(UserAccount.profile),
            selectinload(ProGrant.revoker).selectinload(UserAccount.profile),
        )
    )

    if active_only:
        now = datetime.now(timezone.utc)
        query = query.where(
            and_(
                ProGrant.is_active == True,
                or_(
                    ProGrant.expires_at.is_(None),
                    ProGrant.expires_at > now,
                ),
            )
        )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(ProGrant.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    grants = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size

    return {
        "items": [format_grant_response(g) for g in grants],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.post("/grant", response_model=ProGrantResponse, status_code=status.HTTP_201_CREATED)
async def grant_pro_access(
    data: ProGrantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> ProGrantResponse:
    """Grant Pro access to a user. Admin only."""
    # Check if user exists
    user_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == data.user_id)
    )
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user already has an active grant
    existing_grant = await check_user_has_active_grant(db, data.user_id)
    if existing_grant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has an active Pro grant",
        )

    now = datetime.now(timezone.utc)
    expires_at = None
    if data.duration_days:
        expires_at = now + timedelta(days=data.duration_days)

    # Create grant
    grant = ProGrant(
        user_id=data.user_id,
        granted_by=current_user.id,
        grant_type=data.grant_type.value,
        duration_days=data.duration_days,
        starts_at=now,
        expires_at=expires_at,
        reason=data.reason,
        is_active=True,
    )
    db.add(grant)

    # Update user Pro status
    user.is_pro = True
    user.pro_started_at = user.pro_started_at or now
    if expires_at:
        # Only update expires_at if it would extend the current expiration
        if user.pro_expires_at is None or expires_at > user.pro_expires_at:
            user.pro_expires_at = expires_at
    else:
        # Lifetime grant
        user.pro_expires_at = None

    # Log the action
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=data.user_id,
        action=ProAction.GRANT.value,
        reason=data.reason,
        details={
            "grant_type": data.grant_type.value,
            "duration_days": data.duration_days,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )

    await db.commit()

    # Refresh to get relationships
    await db.refresh(grant)
    grant_query = (
        select(ProGrant)
        .options(
            selectinload(ProGrant.user).selectinload(UserAccount.profile),
            selectinload(ProGrant.granter).selectinload(UserAccount.profile),
        )
        .where(ProGrant.id == grant.id)
    )
    grant_result = await db.execute(grant_query)
    grant = grant_result.scalar_one()

    return format_grant_response(grant)


@router.post("/revoke/{grant_id}", response_model=ProGrantResponse)
async def revoke_grant(
    grant_id: int,
    data: ProGrantRevoke,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> ProGrantResponse:
    """Revoke a Pro grant. Admin only."""
    query = (
        select(ProGrant)
        .options(
            selectinload(ProGrant.user).selectinload(UserAccount.profile),
            selectinload(ProGrant.granter).selectinload(UserAccount.profile),
        )
        .where(ProGrant.id == grant_id)
    )
    result = await db.execute(query)
    grant = result.scalar_one_or_none()

    if not grant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grant not found",
        )

    if not grant.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Grant is already inactive",
        )

    now = datetime.now(timezone.utc)

    # Revoke the grant
    grant.is_active = False
    grant.revoked_at = now
    grant.revoked_by = current_user.id
    grant.revoke_reason = data.reason

    # Check if user has other active grants or Stripe subscription
    user = grant.user
    other_grant = None
    other_grant_query = select(ProGrant).where(
        and_(
            ProGrant.user_id == user.id,
            ProGrant.id != grant_id,
            ProGrant.is_active == True,
            or_(
                ProGrant.expires_at.is_(None),
                ProGrant.expires_at > now,
            ),
        )
    )
    other_result = await db.execute(other_grant_query)
    other_grant = other_result.scalar_one_or_none()

    # Only remove Pro status if no other grants and no Stripe subscription
    if not other_grant and not user.pro_stripe_subscription_id:
        user.is_pro = False
        user.pro_expires_at = now

    # Log the action
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=user.id,
        action=ProAction.REVOKE.value,
        reason=data.reason,
        details={"grant_id": grant_id},
    )

    await db.commit()
    await db.refresh(grant)

    # Re-fetch with relationships
    query = (
        select(ProGrant)
        .options(
            selectinload(ProGrant.user).selectinload(UserAccount.profile),
            selectinload(ProGrant.granter).selectinload(UserAccount.profile),
            selectinload(ProGrant.revoker).selectinload(UserAccount.profile),
        )
        .where(ProGrant.id == grant_id)
    )
    result = await db.execute(query)
    grant = result.scalar_one()

    return format_grant_response(grant)


# ============================================================================
# User Pro Status Endpoints
# ============================================================================

@router.get("/users", response_model=UserProStatusListResponse)
async def list_users_pro_status(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = None,
    filter: ProStatusFilter = Query(ProStatusFilter.ALL),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """List all users with Pro status information. Admin only."""
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.is_active == True)
    )

    # Search
    if search:
        query = query.join(UserProfile, isouter=True).where(
            or_(
                UserAccount.email.ilike(f"%{search}%"),
                UserProfile.first_name.ilike(f"%{search}%"),
                UserProfile.last_name.ilike(f"%{search}%"),
            )
        )

    # Filter
    if filter == ProStatusFilter.PRO_ONLY:
        query = query.where(UserAccount.is_pro == True)
    elif filter == ProStatusFilter.FREE_ONLY:
        query = query.where(UserAccount.is_pro == False)
    elif filter == ProStatusFilter.STRIPE_SUBSCRIPTIONS:
        query = query.where(
            and_(
                UserAccount.is_pro == True,
                UserAccount.pro_stripe_subscription_id.isnot(None),
            )
        )
    # MANUAL_GRANTS filter will be handled after query

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(UserAccount.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    # Batch-load active grants for all users on this page
    user_ids = [u.id for u in users]
    grants_by_user_id = {}
    if user_ids:
        now = datetime.now(timezone.utc)
        grants_query = select(ProGrant).where(
            and_(
                ProGrant.user_id.in_(user_ids),
                ProGrant.is_active == True,
                or_(
                    ProGrant.expires_at.is_(None),
                    ProGrant.expires_at > now,
                ),
            )
        )
        grants_result = await db.execute(grants_query)
        for g in grants_result.scalars().all():
            grants_by_user_id[g.user_id] = g
    else:
        now = datetime.now(timezone.utc)

    # Format response
    items = []

    for user in users:
        grant = grants_by_user_id.get(user.id)

        # Skip if filtering for manual grants and user doesn't have one
        if filter == ProStatusFilter.MANUAL_GRANTS and not grant:
            continue

        # Determine Pro source
        pro_source = None
        if user.is_pro:
            if user.pro_stripe_subscription_id:
                pro_source = "stripe"
            elif grant:
                pro_source = "manual_grant"

        items.append(UserProStatus(
            id=user.id,
            email=user.email,
            first_name=user.profile.first_name if user.profile else "",
            last_name=user.profile.last_name if user.profile else "",
            full_name=user.profile.full_name if user.profile else user.email,
            is_pro=user.is_pro,
            pro_source=pro_source,
            plan_type=user.pro_plan_type,
            expires_at=user.pro_expires_at,
            started_at=user.pro_started_at,
            stripe_subscription_id=user.pro_stripe_subscription_id,
            active_grant_id=grant.id if grant else None,
        ))

    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# ============================================================================
# Subscription Management Endpoints
# ============================================================================

@router.post("/extend/{user_id}")
async def extend_subscription(
    user_id: int,
    data: SubscriptionExtend,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Extend a user's Pro subscription. Admin only."""
    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.is_pro:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a Pro subscriber",
        )

    now = datetime.now(timezone.utc)

    # Calculate new expiration
    current_expires = user.pro_expires_at or now
    if current_expires < now:
        current_expires = now

    new_expires = current_expires + timedelta(days=data.days)
    user.pro_expires_at = new_expires

    # Log the action
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=user_id,
        action=ProAction.EXTEND.value,
        reason=data.reason,
        details={
            "days_added": data.days,
            "old_expires_at": current_expires.isoformat(),
            "new_expires_at": new_expires.isoformat(),
        },
    )

    await db.commit()

    return {
        "message": f"Subscription extended by {data.days} days",
        "new_expires_at": new_expires.isoformat(),
    }


@router.post("/cancel/{user_id}")
async def cancel_subscription(
    user_id: int,
    data: SubscriptionCancel,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Cancel a user's Pro subscription. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()

    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.is_pro:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a Pro subscriber",
        )

    now = datetime.now(timezone.utc)
    stripe_cancelled = False

    # Cancel Stripe subscription if exists
    if user.pro_stripe_subscription_id and app_settings.stripe_secret_key:
        stripe.api_key = app_settings.stripe_secret_key
        try:
            if data.immediate:
                # Cancel immediately
                stripe.Subscription.cancel(user.pro_stripe_subscription_id)
            else:
                # Cancel at period end
                stripe.Subscription.modify(
                    user.pro_stripe_subscription_id,
                    cancel_at_period_end=True
                )
            stripe_cancelled = True
        except stripe.error.StripeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to cancel Stripe subscription: {str(e)}",
            )

    if data.immediate:
        user.is_pro = False
        user.pro_expires_at = now

    # Log the action
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=user_id,
        action=ProAction.CANCEL.value,
        reason=data.reason,
        details={
            "immediate": data.immediate,
            "stripe_subscription_id": user.pro_stripe_subscription_id,
            "stripe_cancelled": stripe_cancelled,
        },
    )

    await db.commit()

    return {
        "message": "Subscription cancelled" + (" immediately" if data.immediate else " at end of period"),
        "stripe_cancelled": stripe_cancelled,
    }


@router.post("/refund/{user_id}")
async def refund_payment(
    user_id: int,
    data: RefundCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Refund a payment for a user. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()

    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.pro_stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no Stripe customer ID - cannot process refund",
        )

    if not app_settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe not configured",
        )

    stripe.api_key = app_settings.stripe_secret_key
    refund_result = None

    try:
        # Get the latest payment intent for this customer
        if data.payment_intent_id:
            # Refund specific payment
            refund_result = stripe.Refund.create(
                payment_intent=data.payment_intent_id,
                amount=int(data.amount * 100) if data.amount else None,  # Convert to cents
                reason="requested_by_customer",
            )
        else:
            # Get customer's latest charge
            charges = stripe.Charge.list(customer=user.pro_stripe_customer_id, limit=1)
            if not charges.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No charges found for this customer",
                )

            charge = charges.data[0]
            refund_result = stripe.Refund.create(
                charge=charge.id,
                amount=int(data.amount * 100) if data.amount else None,
                reason="requested_by_customer",
            )
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe refund failed: {str(e)}",
        )

    refunded_amount = refund_result.amount / 100 if refund_result else data.amount

    # Log the action
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=user_id,
        action=ProAction.REFUND.value,
        reason=data.reason,
        details={
            "amount": str(refunded_amount),
            "stripe_customer_id": user.pro_stripe_customer_id,
            "refund_id": refund_result.id if refund_result else None,
        },
    )

    await db.commit()

    return {
        "message": "Refund processed successfully",
        "amount": str(refunded_amount),
        "refund_id": refund_result.id if refund_result else None,
        "currency": refund_result.currency if refund_result else "eur",
    }


# ============================================================================
# Payment History Endpoints
# ============================================================================

@router.get("/payments")
async def list_all_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """List all recent Stripe payments across all users. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()

    if not app_settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe not configured",
        )

    stripe.api_key = app_settings.stripe_secret_key

    try:
        # Calculate starting_after for pagination
        # Stripe uses cursor-based pagination, so we need to handle this differently
        # For simplicity, we'll fetch more and slice
        limit = min(page_size * page, 100)  # Stripe max is 100

        # Get invoices from Stripe
        invoices = stripe.Invoice.list(
            limit=limit,
            expand=["data.customer"],
        )

        # Build lookup of user emails by stripe customer ID
        customer_ids = list(set(
            inv.customer.id if hasattr(inv.customer, 'id') else inv.customer
            for inv in invoices.data if inv.customer
        ))

        # Get users from database
        users_query = select(UserAccount).options(
            selectinload(UserAccount.profile)
        ).where(
            UserAccount.pro_stripe_customer_id.in_(customer_ids)
        )
        users_result = await db.execute(users_query)
        users = users_result.scalars().all()

        # Create lookup
        customer_to_user = {
            u.pro_stripe_customer_id: u for u in users
        }

        # Paginate the results
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_invoices = invoices.data[start_idx:end_idx]

        payments = []
        for invoice in paginated_invoices:
            customer_id = invoice.customer.id if hasattr(invoice.customer, 'id') else invoice.customer
            user = customer_to_user.get(customer_id)

            payments.append({
                "id": invoice.id,
                "number": invoice.number,
                "amount": invoice.amount_paid / 100,  # Convert from cents
                "currency": invoice.currency.upper(),
                "status": invoice.status,
                "created": datetime.fromtimestamp(invoice.created, tz=timezone.utc).isoformat(),
                "paid_at": datetime.fromtimestamp(invoice.status_transitions.paid_at, tz=timezone.utc).isoformat() if invoice.status_transitions.paid_at else None,
                "invoice_pdf": invoice.invoice_pdf,
                "hosted_invoice_url": invoice.hosted_invoice_url,
                "description": invoice.description or "Pro subscription",
                "payment_intent_id": invoice.payment_intent,
                "refunded": False,
                "user_id": user.id if user else None,
                "user_email": user.email if user else (invoice.customer_email or "Unknown"),
                "user_name": user.profile.full_name if user and user.profile else (invoice.customer_name or "Unknown"),
                "stripe_customer_id": customer_id,
            })

        total = len(invoices.data)
        total_pages = (total + page_size - 1) // page_size

        return {
            "items": payments,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_more": invoices.has_more,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch payments: {str(e)}",
        )


@router.get("/payments/{user_id}")
async def get_user_payments(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Get payment history for a user from Stripe. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()

    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.pro_stripe_customer_id:
        return {"payments": [], "has_more": False}

    if not app_settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe not configured",
        )

    stripe.api_key = app_settings.stripe_secret_key

    try:
        # Get invoices for this customer
        invoices = stripe.Invoice.list(
            customer=user.pro_stripe_customer_id,
            limit=limit,
        )

        payments = []
        for invoice in invoices.data:
            payments.append({
                "id": invoice.id,
                "number": invoice.number,
                "amount": invoice.amount_paid / 100,  # Convert from cents
                "currency": invoice.currency.upper(),
                "status": invoice.status,
                "created": datetime.fromtimestamp(invoice.created, tz=timezone.utc).isoformat(),
                "paid_at": datetime.fromtimestamp(invoice.status_transitions.paid_at, tz=timezone.utc).isoformat() if invoice.status_transitions.paid_at else None,
                "invoice_pdf": invoice.invoice_pdf,
                "hosted_invoice_url": invoice.hosted_invoice_url,
                "description": invoice.description or f"Pro subscription",
                "payment_intent_id": invoice.payment_intent,
                "refunded": False,  # Would check charges for refund status
            })

        return {
            "payments": payments,
            "has_more": invoices.has_more,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch payments: {str(e)}",
        )


@router.post("/payments/{user_id}/sync")
async def sync_subscription_from_stripe(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Sync user's subscription status from Stripe. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()
    from app.models.pro import ProSubscription, SubscriptionStatus

    query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.pro_stripe_customer_id:
        return {"message": "User has no Stripe customer ID", "synced": False}

    if not app_settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe not configured",
        )

    stripe.api_key = app_settings.stripe_secret_key

    try:
        # Get active subscriptions from Stripe
        subscriptions = stripe.Subscription.list(
            customer=user.pro_stripe_customer_id,
            status="active",
            limit=1,
        )

        changes = []

        if subscriptions.data:
            sub = subscriptions.data[0]
            plan_type = sub.metadata.get("plan_type", "monthly")

            # Update user account
            if not user.is_pro:
                user.is_pro = True
                changes.append("Set is_pro=True")

            if user.pro_stripe_subscription_id != sub.id:
                user.pro_stripe_subscription_id = sub.id
                changes.append(f"Updated subscription ID to {sub.id}")

            if user.pro_plan_type != plan_type:
                user.pro_plan_type = plan_type
                changes.append(f"Updated plan type to {plan_type}")

            # Update or create pro_subscriptions record
            sub_query = select(ProSubscription).where(
                ProSubscription.stripe_subscription_id == sub.id
            )
            sub_result = await db.execute(sub_query)
            pro_sub = sub_result.scalar_one_or_none()

            if not pro_sub:
                pro_sub = ProSubscription(
                    user_id=user_id,
                    stripe_subscription_id=sub.id,
                    stripe_customer_id=user.pro_stripe_customer_id,
                    plan_type=plan_type,
                    status=SubscriptionStatus.ACTIVE.value,
                    current_period_start=datetime.fromtimestamp(sub.current_period_start, tz=timezone.utc),
                    current_period_end=datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc),
                )
                db.add(pro_sub)
                changes.append("Created pro_subscriptions record")
            else:
                pro_sub.status = SubscriptionStatus.ACTIVE.value
                pro_sub.current_period_end = datetime.fromtimestamp(sub.current_period_end, tz=timezone.utc)
                changes.append("Updated pro_subscriptions record")

        else:
            # No active subscription in Stripe
            if user.is_pro and user.pro_stripe_subscription_id:
                # Check if there's a manual grant
                grant = await check_user_has_active_grant(db, user_id)
                if not grant:
                    user.is_pro = False
                    changes.append("Set is_pro=False (no active subscription or grant)")

        if changes:
            # Log the sync action
            await log_pro_action(
                db=db,
                admin_id=current_user.id,
                user_id=user_id,
                action="sync_from_stripe",
                details={"changes": changes},
            )
            await db.commit()

        return {
            "message": "Subscription synced from Stripe",
            "synced": True,
            "changes": changes,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync from Stripe: {str(e)}",
        )


@router.post("/payments/resend-invoice")
async def resend_invoice(
    invoice_id: str = Query(..., description="Stripe invoice ID"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Resend an invoice email to the customer. Admin only."""
    import stripe
    from app.config import get_settings
    app_settings = get_settings()

    if not app_settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe not configured",
        )

    stripe.api_key = app_settings.stripe_secret_key

    try:
        invoice = stripe.Invoice.retrieve(invoice_id)

        if invoice.status == "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot resend draft invoice",
            )

        # Send invoice email
        stripe.Invoice.send_invoice(invoice_id)

        return {
            "message": "Invoice email sent successfully",
            "invoice_id": invoice_id,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to resend invoice: {str(e)}",
        )


# ============================================================================
# Audit Log Endpoints
# ============================================================================

@router.get("/audit-log", response_model=AuditLogListResponse)
async def list_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    action: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """List Pro audit log entries. Admin only."""
    query = (
        select(ProAuditLog)
        .options(
            selectinload(ProAuditLog.admin).selectinload(UserAccount.profile),
            selectinload(ProAuditLog.user).selectinload(UserAccount.profile),
        )
    )

    if user_id:
        query = query.where(ProAuditLog.user_id == user_id)
    if admin_id:
        query = query.where(ProAuditLog.admin_id == admin_id)
    if action:
        query = query.where(ProAuditLog.action == action)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(ProAuditLog.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    items = []
    for log in logs:
        items.append(AuditLogEntry(
            id=log.id,
            admin_id=log.admin_id,
            admin_email=log.admin.email if log.admin else "",
            admin_name=log.admin.profile.full_name if log.admin and log.admin.profile else "",
            user_id=log.user_id,
            user_email=log.user.email if log.user else "",
            user_name=log.user.profile.full_name if log.user and log.user.profile else "",
            action=log.action,
            details=log.details,
            reason=log.reason,
            stripe_event_id=log.stripe_event_id,
            created_at=log.created_at,
        ))

    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/audit-log/export")
async def export_audit_log(
    user_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    action: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> StreamingResponse:
    """Export audit log to CSV. Admin only."""
    query = (
        select(ProAuditLog)
        .options(
            selectinload(ProAuditLog.admin).selectinload(UserAccount.profile),
            selectinload(ProAuditLog.user).selectinload(UserAccount.profile),
        )
    )

    if user_id:
        query = query.where(ProAuditLog.user_id == user_id)
    if admin_id:
        query = query.where(ProAuditLog.admin_id == admin_id)
    if action:
        query = query.where(ProAuditLog.action == action)

    query = query.order_by(ProAuditLog.created_at.desc())

    result = await db.execute(query)
    logs = result.scalars().all()

    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Admin Email", "Admin Name", "User Email", "User Name",
        "Action", "Reason", "Details", "Stripe Event ID"
    ])

    for log in logs:
        writer.writerow([
            log.created_at.isoformat(),
            log.admin.email if log.admin else "",
            log.admin.profile.full_name if log.admin and log.admin.profile else "",
            log.user.email if log.user else "",
            log.user.profile.full_name if log.user and log.user.profile else "",
            log.action,
            log.reason or "",
            str(log.details) if log.details else "",
            log.stripe_event_id or "",
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=pro_audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        },
    )


# ============================================================================
# Settings Endpoints
# ============================================================================

@router.get("/settings", response_model=list[ProSettingResponse])
async def list_settings(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[ProSettingResponse]:
    """List all Pro settings. Admin only."""
    query = (
        select(ProSettings)
        .options(selectinload(ProSettings.updater).selectinload(UserAccount.profile))
        .order_by(ProSettings.key)
    )
    result = await db.execute(query)
    settings = result.scalars().all()

    return [
        ProSettingResponse(
            id=s.id,
            key=s.key,
            value=s.value,
            description=s.description,
            updated_by=s.updated_by,
            updater_name=s.updater.profile.full_name if s.updater and s.updater.profile else None,
            updated_at=s.updated_at,
        )
        for s in settings
    ]


@router.patch("/settings/{key}", response_model=ProSettingResponse)
async def update_setting(
    key: str,
    data: ProSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> ProSettingResponse:
    """Update a Pro setting. Admin only."""
    query = select(ProSettings).where(ProSettings.key == key)
    result = await db.execute(query)
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found",
        )

    old_value = setting.value
    setting.value = data.value
    setting.updated_by = current_user.id
    setting.updated_at = datetime.now(timezone.utc)

    # Log the change
    await log_pro_action(
        db=db,
        admin_id=current_user.id,
        user_id=current_user.id,  # Self action
        action="setting_change",
        details={
            "key": key,
            "old_value": old_value,
            "new_value": data.value,
        },
    )

    await db.commit()
    await db.refresh(setting)

    return ProSettingResponse(
        id=setting.id,
        key=setting.key,
        value=setting.value,
        description=setting.description,
        updated_by=setting.updated_by,
        updater_name=current_user.profile.full_name if current_user.profile else None,
        updated_at=setting.updated_at,
    )


# ============================================================================
# Account Deletion Management Endpoints
# ============================================================================

from app.services.account_deletion import account_deletion_service, AccountDeletionError
from pydantic import BaseModel, Field


class PendingDeletionUser(BaseModel):
    """User pending deletion."""
    id: int
    email: str
    first_name: str
    last_name: str
    deletion_scheduled_at: datetime
    permanent_deletion_at: datetime
    days_remaining: int
    can_recover: bool


class DeletedUser(BaseModel):
    """Permanently deleted user."""
    id: int
    display_name: str
    deleted_at: Optional[datetime]


class ForceRecoverRequest(BaseModel):
    """Request to force recover an account."""
    reason: str = Field(..., min_length=5, max_length=500)


@router.get("/deleted-users/pending")
async def list_pending_deletion_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    List users with pending deletion (in grace period).
    Admin only.
    """
    result = await account_deletion_service.get_pending_deletion_users(db, page, page_size)

    # Enrich with user details
    items = []
    for item in result["items"]:
        user_query = (
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.id == item["id"])
        )
        user_result = await db.execute(user_query)
        user = user_result.scalar_one_or_none()

        if user:
            items.append({
                "id": user.id,
                "email": item["email"],  # Keep original email (not yet anonymized)
                "first_name": user.profile.first_name if user.profile else "",
                "last_name": user.profile.last_name if user.profile else "",
                "deletion_scheduled_at": item["deletion_scheduled_at"].isoformat(),
                "permanent_deletion_at": item["permanent_deletion_at"].isoformat(),
                "days_remaining": item["days_remaining"],
                "can_recover": item["can_recover"]
            })

    return {
        "items": items,
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "total_pages": result["total_pages"],
        "grace_period_days": result["grace_period_days"]
    }


@router.get("/deleted-users/permanent")
async def list_permanently_deleted_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    List permanently deleted (anonymized) users.
    Admin only.
    """
    return await account_deletion_service.get_deleted_users(db, page, page_size)


@router.post("/deleted-users/{user_id}/recover")
async def force_recover_user(
    user_id: int,
    data: ForceRecoverRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Force recover a user's account (admin action).
    Only works for accounts in grace period (not yet permanently deleted).
    Admin only.
    """
    try:
        result = await account_deletion_service.admin_force_recover(
            user_id=user_id,
            admin_id=current_user.id,
            reason=data.reason,
            db=db
        )

        return {
            "message": result["message"],
            "recovered_at": result["recovered_at"].isoformat(),
            "admin_id": result["admin_id"]
        }

    except AccountDeletionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/deleted-users/{user_id}/anonymize")
async def anonymize_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """Manually anonymize a single user's account. Admin only."""
    result = await account_deletion_service.permanently_anonymize(user_id, db)
    return result


@router.post("/deleted-users/process-expired")
async def process_expired_deletions(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Manually trigger processing of expired account deletions.
    This is normally run by a background job daily.
    Admin only.
    """
    count = await account_deletion_service.process_expired_deletions(db)

    return {
        "message": f"Processed {count} expired account deletions",
        "accounts_anonymized": count
    }
