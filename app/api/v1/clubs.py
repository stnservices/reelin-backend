"""Club management endpoints."""

from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.club import Club, ClubMembership, MembershipStatus, MembershipRole
from app.models.notification import UserDeviceToken
from app.services.push_notifications import send_push_notification
from app.schemas.club import (
    ClubUpdate,
    ClubResponse,
    ClubDetailResponse,
    ClubListResponse,
    MemberInvite,
    MembershipUpdate,
    MembershipDetailResponse,
    MembershipListResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter()


@router.get("/my", response_model=ClubDetailResponse)
async def get_my_club(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get the club owned by the current user.
    Returns 404 if user doesn't own a club.
    """
    query = (
        select(Club)
        .options(selectinload(Club.owner).selectinload(UserAccount.profile))
        .where(
            Club.owner_id == current_user.id,
            Club.is_deleted == False,
            Club.is_active == True
        )
    )
    result = await db.execute(query)
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="You do not own a club")

    # Get member count
    member_count_query = select(func.count(ClubMembership.id)).where(
        ClubMembership.club_id == club.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    member_count_result = await db.execute(member_count_query)
    member_count = member_count_result.scalar()

    return ClubDetailResponse.from_club(club, member_count)


@router.get("/invitations", response_model=MembershipListResponse)
async def get_my_pending_invitations(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get pending club invitations for the current user.
    Returns invitations with status 'invited'.
    """
    query = (
        select(ClubMembership)
        .options(
            selectinload(ClubMembership.user).selectinload(UserAccount.profile),
            selectinload(ClubMembership.invited_by).selectinload(UserAccount.profile),
            selectinload(ClubMembership.club),
        )
        .where(
            ClubMembership.user_id == current_user.id,
            ClubMembership.status == MembershipStatus.INVITED.value,
        )
        .order_by(ClubMembership.invited_at.desc())
    )

    result = await db.execute(query)
    memberships = result.scalars().all()

    return MembershipListResponse(
        items=[MembershipDetailResponse.from_membership(m) for m in memberships],
        total=len(memberships),
        page=1,
        page_size=len(memberships),
        pages=1,
    )


@router.get("/memberships", response_model=ClubListResponse)
async def get_my_memberships(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get clubs where the current user is an active member.
    Useful for validators to see clubs they belong to.
    """
    # Get club IDs where user is an active member
    membership_query = select(ClubMembership.club_id).where(
        ClubMembership.user_id == current_user.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    membership_result = await db.execute(membership_query)
    club_ids = [row[0] for row in membership_result.fetchall()]

    if not club_ids:
        return ClubListResponse(items=[], total=0, page=1, page_size=20, pages=1)

    # Get clubs
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.id.in_(club_ids), Club.is_deleted == False)
        .order_by(Club.name)
    )

    result = await db.execute(query)
    clubs = result.scalars().all()

    # Get member counts
    items = []
    for club in clubs:
        member_count_query = select(func.count(ClubMembership.id)).where(
            ClubMembership.club_id == club.id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
        )
        member_count_result = await db.execute(member_count_query)
        member_count = member_count_result.scalar()
        items.append(ClubDetailResponse.from_club(club, member_count))

    return ClubListResponse(
        items=items,
        total=len(items),
        page=1,
        page_size=len(items),
        pages=1,
    )


@router.get("", response_model=ClubListResponse)
async def list_clubs(
    search: str | None = Query(None, min_length=1),
    organizers_only: bool = Query(False, description="Filter to only clubs whose owners can organize events"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    List all active clubs (public endpoint).

    Use organizers_only=true to filter to clubs whose owners have the organizer role.
    This is useful for notification settings where users only want to follow clubs
    that can create events.
    """
    from app.models.user import UserProfile

    # Build query
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.is_active == True, Club.is_deleted == False)
    )

    # Filter by organizer role if requested
    if organizers_only:
        query = query.join(UserAccount, Club.owner_id == UserAccount.id)
        query = query.join(UserProfile, UserAccount.id == UserProfile.user_id)
        query = query.where(UserProfile.roles.contains(["organizer"]))

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.where(
            (Club.name.ilike(search_term)) | (Club.acronym.ilike(search_term))
        )

    # Get total count
    count_query = select(func.count(Club.id)).where(
        Club.is_active == True, Club.is_deleted == False
    )
    # Apply same filters to count query
    if organizers_only:
        count_query = count_query.join(UserAccount, Club.owner_id == UserAccount.id)
        count_query = count_query.join(UserProfile, UserAccount.id == UserProfile.user_id)
        count_query = count_query.where(UserProfile.roles.contains(["organizer"]))
    if search:
        search_term = f"%{search}%"
        count_query = count_query.where(
            (Club.name.ilike(search_term)) | (Club.acronym.ilike(search_term))
        )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(Club.name).offset(offset).limit(page_size)

    result = await db.execute(query)
    clubs = result.scalars().all()

    # Get member counts
    items = []
    for club in clubs:
        member_count_query = select(func.count(ClubMembership.id)).where(
            ClubMembership.club_id == club.id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
        )
        member_count_result = await db.execute(member_count_query)
        member_count = member_count_result.scalar()
        items.append(ClubDetailResponse.from_club(club, member_count))

    return ClubListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.get("/{club_id}", response_model=ClubDetailResponse)
async def get_club(
    club_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get a specific club (public endpoint).
    """
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.id == club_id, Club.is_deleted == False)
    )
    result = await db.execute(query)
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Get member count
    member_count_query = select(func.count(ClubMembership.id)).where(
        ClubMembership.club_id == club.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    member_count_result = await db.execute(member_count_query)
    member_count = member_count_result.scalar()

    return ClubDetailResponse.from_club(club, member_count)


@router.patch("/{club_id}", response_model=ClubDetailResponse)
async def update_club(
    club_id: int,
    update_data: ClubUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update a club.
    Only club owner or admins can update.
    """
    query = (
        select(Club)
        .options(selectinload(Club.owner).selectinload(UserAccount.profile))
        .where(Club.id == club_id, Club.is_deleted == False)
    )
    result = await db.execute(query)
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_system_admin = "administrator" in user_roles
    is_owner = club.owner_id == current_user.id

    # Check if user is club admin
    membership_query = select(ClubMembership).where(
        ClubMembership.club_id == club_id,
        ClubMembership.user_id == current_user.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    membership_result = await db.execute(membership_query)
    membership = membership_result.scalar_one_or_none()
    is_club_admin = membership and membership.is_admin

    if not is_system_admin and not is_owner and not is_club_admin:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this club",
        )

    # Update fields
    if update_data.name is not None:
        # Check uniqueness
        existing = await db.execute(
            select(Club).where(
                Club.name == update_data.name,
                Club.id != club_id,
                Club.is_deleted == False,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Club name already exists")
        club.name = update_data.name

    if update_data.acronym is not None:
        existing = await db.execute(
            select(Club).where(
                Club.acronym == update_data.acronym.upper(),
                Club.id != club_id,
                Club.is_deleted == False,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Club acronym already exists")
        club.acronym = update_data.acronym.upper()

    if update_data.description is not None:
        club.description = update_data.description

    if update_data.logo_url is not None:
        club.logo_url = update_data.logo_url

    if update_data.country_id is not None:
        club.country_id = update_data.country_id if update_data.country_id > 0 else None

    if update_data.city_id is not None:
        club.city_id = update_data.city_id if update_data.city_id > 0 else None

    if update_data.is_active is not None and (is_system_admin or is_owner):
        club.is_active = update_data.is_active

    await db.commit()

    # Reload with all relationships
    query = (
        select(Club)
        .options(
            selectinload(Club.owner).selectinload(UserAccount.profile),
            selectinload(Club.country),
            selectinload(Club.city),
        )
        .where(Club.id == club_id)
    )
    result = await db.execute(query)
    club = result.scalar_one()

    # Get member count
    member_count_query = select(func.count(ClubMembership.id)).where(
        ClubMembership.club_id == club.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    member_count_result = await db.execute(member_count_query)
    member_count = member_count_result.scalar()

    return ClubDetailResponse.from_club(club, member_count)


@router.delete("/{club_id}", response_model=MessageResponse)
async def delete_club(
    club_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Soft delete a club.
    Only club owner or system admins can delete.
    """
    query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    result = await db.execute(query)
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check permissions
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_system_admin = "administrator" in user_roles
    is_owner = club.owner_id == current_user.id

    if not is_system_admin and not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this club",
        )

    # Soft delete
    club.is_deleted = True
    club.deleted_at = datetime.now(timezone.utc)
    club.is_active = False

    await db.commit()

    return {"message": "Club deleted successfully"}


# ============================================================================
# Membership Management
# ============================================================================


@router.get("/{club_id}/members", response_model=MembershipListResponse)
async def list_club_members(
    club_id: int,
    status_filter: MembershipStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List club members.
    Only active members can see the full list.
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Club members are publicly visible - no membership check needed
    # All authenticated users can see club member lists

    # Build query
    query = (
        select(ClubMembership)
        .options(
            selectinload(ClubMembership.user).selectinload(UserAccount.profile),
            selectinload(ClubMembership.invited_by),
            selectinload(ClubMembership.dismissed_by),
        )
        .where(ClubMembership.club_id == club_id)
    )

    if status_filter:
        query = query.where(ClubMembership.status == status_filter.value)

    # Get total count
    count_query = select(func.count(ClubMembership.id)).where(
        ClubMembership.club_id == club_id
    )
    if status_filter:
        count_query = count_query.where(ClubMembership.status == status_filter.value)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Pagination
    offset = (page - 1) * page_size
    query = query.order_by(ClubMembership.joined_at.desc().nulls_last()).offset(offset).limit(page_size)

    result = await db.execute(query)
    memberships = result.scalars().all()

    return MembershipListResponse(
        items=[MembershipDetailResponse.from_membership(m) for m in memberships],
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("/{club_id}/members", response_model=MembershipDetailResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    club_id: int,
    invite_data: MemberInvite,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Invite a user to join the club by email address.
    Only club admins can invite members.

    GDPR Compliant: Uses email address instead of user search to protect user privacy.
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check if current user is club admin
    admin_check_query = select(ClubMembership).where(
        ClubMembership.club_id == club_id,
        ClubMembership.user_id == current_user.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
        ClubMembership.role == MembershipRole.ADMIN.value,
    )
    admin_check_result = await db.execute(admin_check_query)
    is_club_admin = admin_check_result.scalar_one_or_none() is not None

    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_system_admin = "administrator" in user_roles
    is_owner = club.owner_id == current_user.id

    if not is_club_admin and not is_system_admin and not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to invite members",
        )

    # Look up user by email (case-insensitive)
    user_query = select(UserAccount).where(
        func.lower(UserAccount.email) == invite_data.email.lower()
    )
    user_result = await db.execute(user_query)
    user_to_invite = user_result.scalar_one_or_none()

    if not user_to_invite:
        raise HTTPException(
            status_code=404,
            detail="No account found with this email address"
        )

    # Check if user owns a club (cannot be invited to another club)
    owned_club_query = select(Club).where(
        Club.owner_id == user_to_invite.id,
        Club.is_deleted == False,
        Club.is_active == True,
    )
    owned_result = await db.execute(owned_club_query)
    if owned_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="This user owns a club and cannot be invited to another club",
        )

    # Check if user is already an active member of another club
    other_membership_query = select(ClubMembership).where(
        ClubMembership.user_id == user_to_invite.id,
        ClubMembership.club_id != club_id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    other_result = await db.execute(other_membership_query)
    if other_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="This user is already a member of another club",
        )

    # Check if already a member of this club (allow reinvite if dismissed)
    existing_membership_query = await db.execute(
        select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.user_id == user_to_invite.id,
        )
    )
    existing_membership = existing_membership_query.scalar_one_or_none()

    if existing_membership:
        if existing_membership.status in [MembershipStatus.ACTIVE.value, MembershipStatus.INVITED.value]:
            raise HTTPException(
                status_code=409,
                detail="User is already a member or has a pending invitation",
            )
        elif existing_membership.status == MembershipStatus.DISMISSED.value:
            # Reinvite dismissed member - update existing record
            existing_membership.role = invite_data.role.value
            existing_membership.status = MembershipStatus.INVITED.value
            existing_membership.permissions = invite_data.permissions
            existing_membership.invited_by_id = current_user.id
            existing_membership.invited_at = datetime.now(timezone.utc)
            existing_membership.dismissed_at = None
            existing_membership.dismissed_by_id = None
            existing_membership.joined_at = None
            membership = existing_membership
            await db.flush()
    else:
        # Create new invitation
        membership = ClubMembership(
            club_id=club_id,
            user_id=user_to_invite.id,
            role=invite_data.role.value,
            status=MembershipStatus.INVITED.value,
            permissions=invite_data.permissions,
            invited_by_id=current_user.id,
            invited_at=datetime.now(timezone.utc),
        )
        db.add(membership)
        await db.flush()  # Get the membership ID

    # Create in-app notification for the invited user
    from app.api.v1.notifications import create_notification

    inviter_name = current_user.email
    if current_user.profile:
        if current_user.profile.first_name or current_user.profile.last_name:
            inviter_name = f"{current_user.profile.first_name or ''} {current_user.profile.last_name or ''}".strip()

    await create_notification(
        db=db,
        user_id=user_to_invite.id,
        notification_type="club_invitation",
        title=f"Club Invitation: {club.name}",
        message=f"You have been invited to join {club.name} by {inviter_name}",
        data={
            "club_id": club.id,
            "club_name": club.name,
            "membership_id": membership.id,
            "invited_by_email": current_user.email,
            "invited_by_name": inviter_name,
        },
    )

    # Get user's device tokens for push notification
    token_query = select(UserDeviceToken.token).where(
        UserDeviceToken.user_id == user_to_invite.id
    )
    token_result = await db.execute(token_query)
    tokens = list(token_result.scalars().all())

    await db.commit()

    # Send push notification (after commit so notification is saved)
    if tokens:
        send_push_notification(
            tokens=tokens,
            title=f"Club Invitation: {club.name}",
            body=f"You have been invited to join {club.name} by {inviter_name}",
            data={
                "type": "club_invitation",
                "club_id": str(club.id),
                "membership_id": str(membership.id),
            },
            click_action=f"/clubs/invitations",
        )

    # Reload with relationships
    query = (
        select(ClubMembership)
        .options(
            selectinload(ClubMembership.user).selectinload(UserAccount.profile),
            selectinload(ClubMembership.invited_by),
        )
        .where(ClubMembership.id == membership.id)
    )
    result = await db.execute(query)
    membership = result.scalar_one()

    return MembershipDetailResponse.from_membership(membership)


@router.post("/{club_id}/members/{membership_id}/accept", response_model=MessageResponse)
async def accept_invitation(
    club_id: int,
    membership_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Accept a club invitation.
    Only the invited user can accept.
    """
    query = select(ClubMembership).where(
        ClubMembership.id == membership_id,
        ClubMembership.club_id == club_id,
    )
    result = await db.execute(query)
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if membership.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to accept this invitation",
        )

    if membership.status != MembershipStatus.INVITED.value:
        raise HTTPException(
            status_code=400,
            detail="Invitation has already been processed",
        )

    # Check if user is already an active member of another club
    existing_membership_query = select(ClubMembership).where(
        ClubMembership.user_id == current_user.id,
        ClubMembership.club_id != club_id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
    )
    existing_result = await db.execute(existing_membership_query)
    existing_membership = existing_result.scalar_one_or_none()

    if existing_membership:
        raise HTTPException(
            status_code=409,
            detail="You are already a member of another club. Leave that club first to join a new one.",
        )

    # Check if user already owns a club
    owned_club_query = select(Club).where(
        Club.owner_id == current_user.id,
        Club.is_deleted == False,
        Club.is_active == True,
    )
    owned_result = await db.execute(owned_club_query)
    owned_club = owned_result.scalar_one_or_none()

    if owned_club:
        raise HTTPException(
            status_code=409,
            detail="You already own a club. You cannot join another club while owning one.",
        )

    membership.status = MembershipStatus.ACTIVE.value
    membership.joined_at = datetime.now(timezone.utc)

    await db.commit()

    return {"message": "Invitation accepted"}


@router.post("/{club_id}/members/{membership_id}/reject", response_model=MessageResponse)
async def reject_invitation(
    club_id: int,
    membership_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Reject a club invitation.
    Only the invited user can reject.
    """
    query = select(ClubMembership).where(
        ClubMembership.id == membership_id,
        ClubMembership.club_id == club_id,
    )
    result = await db.execute(query)
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if membership.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to reject this invitation",
        )

    if membership.status != MembershipStatus.INVITED.value:
        raise HTTPException(
            status_code=400,
            detail="Invitation has already been processed",
        )

    await db.delete(membership)
    await db.commit()

    return {"message": "Invitation rejected"}


@router.patch("/{club_id}/members/{membership_id}", response_model=MembershipDetailResponse)
async def update_membership(
    club_id: int,
    membership_id: int,
    update_data: MembershipUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update a member's role or permissions.
    Only club admins can update memberships.
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check permissions
    admin_check_query = select(ClubMembership).where(
        ClubMembership.club_id == club_id,
        ClubMembership.user_id == current_user.id,
        ClubMembership.status == MembershipStatus.ACTIVE.value,
        ClubMembership.role == MembershipRole.ADMIN.value,
    )
    admin_check_result = await db.execute(admin_check_query)
    is_club_admin = admin_check_result.scalar_one_or_none() is not None

    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_system_admin = "administrator" in user_roles
    is_owner = club.owner_id == current_user.id

    if not is_club_admin and not is_system_admin and not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update memberships",
        )

    # Get membership
    query = (
        select(ClubMembership)
        .options(
            selectinload(ClubMembership.user).selectinload(UserAccount.profile),
            selectinload(ClubMembership.invited_by),
        )
        .where(
            ClubMembership.id == membership_id,
            ClubMembership.club_id == club_id,
        )
    )
    result = await db.execute(query)
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Cannot demote the owner
    if membership.user_id == club.owner_id and update_data.role == MembershipRole.MEMBER:
        raise HTTPException(
            status_code=400,
            detail="Cannot demote the club owner",
        )

    if update_data.role is not None:
        membership.role = update_data.role.value

    if update_data.permissions is not None:
        membership.permissions = update_data.permissions

    await db.commit()
    await db.refresh(membership)

    return MembershipDetailResponse.from_membership(membership)


@router.delete("/{club_id}/members/{membership_id}", response_model=MessageResponse)
async def remove_member(
    club_id: int,
    membership_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Remove a member from the club or cancel a pending invitation.
    Club admins can remove members and cancel invitations.
    Members can leave the club themselves.
    Invited users can reject their own invitation.

    - For ACTIVE members: marks as DISMISSED
    - For INVITED (pending invitations): DELETES the record so they can be re-invited
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Get membership
    query = select(ClubMembership).where(
        ClubMembership.id == membership_id,
        ClubMembership.club_id == club_id,
    )
    result = await db.execute(query)
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Check if it's self-removal
    is_self_removal = membership.user_id == current_user.id

    # Cannot remove the owner
    if membership.user_id == club.owner_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the club owner",
        )

    if not is_self_removal:
        # Check if current user is admin
        admin_check_query = select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.user_id == current_user.id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
            ClubMembership.role == MembershipRole.ADMIN.value,
        )
        admin_check_result = await db.execute(admin_check_query)
        is_club_admin = admin_check_result.scalar_one_or_none() is not None

        user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
        is_system_admin = "administrator" in user_roles
        is_owner = club.owner_id == current_user.id

        if not is_club_admin and not is_system_admin and not is_owner:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to remove members",
            )

    # Handle pending invitations: DELETE so they can be re-invited
    if membership.status == MembershipStatus.INVITED.value:
        await db.delete(membership)
        await db.commit()
        if is_self_removal:
            return {"message": "Invitation rejected"}
        return {"message": "Invitation cancelled"}

    # Handle active members: mark as dismissed
    membership.status = MembershipStatus.DISMISSED.value
    membership.dismissed_at = datetime.now(timezone.utc)
    # Only set dismissed_by if someone else dismissed them (not self-removal)
    membership.dismissed_by_id = None if is_self_removal else current_user.id
    await db.commit()

    if is_self_removal:
        return {"message": "You have left the club"}
    return {"message": "Member removed from club"}


# ============================================================================
# Club Validators (Quick access for event assignment)
# ============================================================================


@router.get("/{club_id}/validators")
async def list_club_validators(
    club_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    List all users in the club who have the validator system role.
    Used by organizers when assigning validators to their events.

    Returns club members who:
    1. Have active membership in this club
    2. Have 'validator' in their system roles (UserProfile.roles)
    """
    # Check club exists
    club_query = select(Club).where(Club.id == club_id, Club.is_deleted == False)
    club_result = await db.execute(club_query)
    club = club_result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check if current user is club owner, admin, or system admin
    user_roles = set(current_user.profile.roles or []) if current_user.profile else set()
    is_system_admin = "administrator" in user_roles
    is_owner = club.owner_id == current_user.id

    # Check if user is club admin
    if not is_system_admin and not is_owner:
        admin_check_query = select(ClubMembership).where(
            ClubMembership.club_id == club_id,
            ClubMembership.user_id == current_user.id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
            ClubMembership.role == MembershipRole.ADMIN.value,
        )
        admin_check_result = await db.execute(admin_check_query)
        is_club_admin = admin_check_result.scalar_one_or_none() is not None

        if not is_club_admin:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view club validators",
            )

    # Get active club members who have validator role in their system roles
    from app.models.user import UserProfile

    query = (
        select(ClubMembership)
        .options(
            selectinload(ClubMembership.user).selectinload(UserAccount.profile),
        )
        .join(UserAccount, ClubMembership.user_id == UserAccount.id)
        .join(UserProfile, UserAccount.id == UserProfile.user_id)
        .where(
            ClubMembership.club_id == club_id,
            ClubMembership.status == MembershipStatus.ACTIVE.value,
            UserProfile.roles.contains(["validator"]),  # Has validator system role
        )
        .order_by(UserProfile.first_name, UserProfile.last_name)
    )

    result = await db.execute(query)
    memberships = result.scalars().all()

    return {
        "club_id": club_id,
        "club_name": club.name,
        "validators": [
            {
                "user_id": m.user.id,
                "email": m.user.email,
                "first_name": m.user.profile.first_name if m.user.profile else None,
                "last_name": m.user.profile.last_name if m.user.profile else None,
                "profile_picture_url": m.user.profile.profile_picture_url if m.user.profile else None,
                "club_role": m.role,
                "joined_at": m.joined_at.isoformat() if m.joined_at else None,
            }
            for m in memberships
        ],
        "count": len(memberships),
    }
