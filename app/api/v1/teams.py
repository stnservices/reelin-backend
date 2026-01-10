"""Team management endpoints."""

from math import ceil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import UserAccount
from app.models.event import Event
from app.models.enrollment import EventEnrollment, EnrollmentStatus
from app.models.team import Team, TeamMember, TeamMemberRole
from app.schemas.team import (
    TeamCreate,
    TeamUpdate,
    TeamResponse,
    TeamDetailResponse,
    TeamListResponse,
    TeamMemberCreate,
    TeamMemberResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter()


def team_to_response(team: Team) -> dict:
    """Convert team model to response dict."""
    return {
        "id": team.id,
        "event_id": team.event_id,
        "name": team.name,
        "team_number": team.team_number,
        "description": team.description,
        "logo_url": team.logo_url,
        "is_active": team.is_active,
        "created_at": team.created_at,
        "updated_at": team.updated_at,
        "created_by_id": team.created_by_id,
        "created_by_name": f"{team.created_by.profile.first_name} {team.created_by.profile.last_name}" if team.created_by and team.created_by.profile else None,
        "member_count": sum(1 for m in team.members if m.is_active),
    }


def member_to_response(member: TeamMember) -> dict:
    """Convert team member model to response dict."""
    return {
        "id": member.id,
        "team_id": member.team_id,
        "enrollment_id": member.enrollment_id,
        "role": member.role,
        "is_active": member.is_active,
        "added_at": member.added_at,
        "user_id": member.enrollment.user_id if member.enrollment else None,
        "user_first_name": member.enrollment.user.profile.first_name if member.enrollment and member.enrollment.user and member.enrollment.user.profile else None,
        "user_last_name": member.enrollment.user.profile.last_name if member.enrollment and member.enrollment.user and member.enrollment.user.profile else None,
    }


@router.get("/{event_id}/teams", response_model=TeamListResponse)
async def list_event_teams(
    event_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """List all teams for an event."""
    # Verify event exists and is a team event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if not event.is_team_event:
        raise HTTPException(
            status_code=400,
            detail="This event does not support teams"
        )

    # Get teams with members and full user info
    query = (
        select(Team)
        .options(
            selectinload(Team.created_by).selectinload(UserAccount.profile),
            selectinload(Team.members)
                .selectinload(TeamMember.enrollment)
                .selectinload(EventEnrollment.user)
                .selectinload(UserAccount.profile),
        )
        .where(Team.event_id == event_id, Team.is_active == True)
    )

    # Get total count
    count_query = select(func.count(Team.id)).where(
        Team.event_id == event_id, Team.is_active == True
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(Team.team_number, Team.created_at)
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    teams = result.scalars().all()

    # Build response with members included for each team
    team_responses = []
    for t in teams:
        response = team_to_response(t)
        response["members"] = [member_to_response(m) for m in t.members if m.is_active]
        team_responses.append(response)

    return TeamListResponse(
        items=team_responses,
        total=total,
        page=page,
        page_size=page_size,
        pages=ceil(total / page_size) if total > 0 else 1,
    )


@router.post("/{event_id}/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    event_id: int,
    team_data: TeamCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Create a new team for an event.
    Only event owner or administrator can create teams.
    """
    # Verify event exists and is a team event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if not event.is_team_event:
        raise HTTPException(
            status_code=400,
            detail="This event does not support teams"
        )

    # Check permissions - only event owner or admin can create teams
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_owner = event.created_by_id == current_user.id

    if not (is_admin or is_owner):
        raise HTTPException(
            status_code=403,
            detail="Only event organizer or administrator can create teams"
        )

    # Check team name uniqueness
    name_query = select(Team).where(
        Team.event_id == event_id,
        Team.name == team_data.name,
        Team.is_active == True,
    )
    name_result = await db.execute(name_query)
    if name_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Team name already exists for this event"
        )

    # Get next team number
    max_number_query = select(func.max(Team.team_number)).where(Team.event_id == event_id)
    max_number_result = await db.execute(max_number_query)
    max_number = max_number_result.scalar() or 0
    team_number = max_number + 1

    # Create team
    team = Team(
        event_id=event_id,
        name=team_data.name,
        description=team_data.description,
        logo_url=team_data.logo_url,
        team_number=team_number,
        created_by_id=current_user.id,
    )
    db.add(team)
    await db.flush()

    # Add initial members if provided
    if team_data.members:
        for member_init in team_data.members:
            # Verify enrollment exists and is approved
            enrollment_query = (
                select(EventEnrollment)
                .where(
                    EventEnrollment.id == member_init.enrollment_id,
                    EventEnrollment.event_id == event_id,
                    EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                )
            )
            enrollment_result = await db.execute(enrollment_query)
            enrollment = enrollment_result.scalar_one_or_none()

            if enrollment:
                # Check not already in another team
                existing_query = (
                    select(TeamMember)
                    .join(Team)
                    .where(
                        Team.event_id == event_id,
                        TeamMember.enrollment_id == member_init.enrollment_id,
                        TeamMember.is_active == True,
                    )
                )
                existing_result = await db.execute(existing_query)
                if not existing_result.scalar_one_or_none():
                    member = TeamMember(
                        team_id=team.id,
                        enrollment_id=member_init.enrollment_id,
                        role=member_init.role,
                        added_by_id=current_user.id,
                    )
                    db.add(member)

    await db.commit()

    # Reload with relationships
    query = (
        select(Team)
        .options(
            selectinload(Team.created_by).selectinload(UserAccount.profile),
            selectinload(Team.members),
        )
        .where(Team.id == team.id)
    )
    result = await db.execute(query)
    team = result.scalar_one()

    return team_to_response(team)


@router.get("/{event_id}/teams/{team_id}", response_model=TeamDetailResponse)
async def get_team(
    event_id: int,
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Get team details with members."""
    query = (
        select(Team)
        .options(
            selectinload(Team.created_by).selectinload(UserAccount.profile),
            selectinload(Team.members).selectinload(TeamMember.enrollment).selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
        )
        .where(Team.id == team_id, Team.event_id == event_id)
    )
    result = await db.execute(query)
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    response = team_to_response(team)
    response["members"] = [member_to_response(m) for m in team.members if m.is_active]

    return response


@router.patch("/{event_id}/teams/{team_id}", response_model=TeamResponse)
async def update_team(
    event_id: int,
    team_id: int,
    team_data: TeamUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Update team details.
    Only team captain or event owner can update.
    """
    query = (
        select(Team)
        .options(
            selectinload(Team.created_by).selectinload(UserAccount.profile),
            selectinload(Team.members),
            selectinload(Team.event),
        )
        .where(Team.id == team_id, Team.event_id == event_id)
    )
    result = await db.execute(query)
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_event_owner = team.event.created_by_id == current_user.id
    is_captain = team.created_by_id == current_user.id

    if not (is_admin or is_event_owner or is_captain):
        raise HTTPException(
            status_code=403,
            detail="Only team captain or event owner can update the team"
        )

    # Check name uniqueness if changing
    if team_data.name and team_data.name != team.name:
        name_query = select(Team).where(
            Team.event_id == event_id,
            Team.name == team_data.name,
            Team.is_active == True,
            Team.id != team_id,
        )
        name_result = await db.execute(name_query)
        if name_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Team name already exists for this event"
            )

    # Apply updates
    update_data = team_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(team, field, value)

    await db.commit()
    await db.refresh(team)

    return team_to_response(team)


@router.delete("/{event_id}/teams/{team_id}", response_model=MessageResponse)
async def delete_team(
    event_id: int,
    team_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Delete (deactivate) a team.
    Only team captain, event owner, or admin can delete.
    """
    query = (
        select(Team)
        .options(selectinload(Team.event))
        .where(Team.id == team_id, Team.event_id == event_id)
    )
    result = await db.execute(query)
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_event_owner = team.event.created_by_id == current_user.id
    is_captain = team.created_by_id == current_user.id

    if not (is_admin or is_event_owner or is_captain):
        raise HTTPException(
            status_code=403,
            detail="Only team captain or event owner can delete the team"
        )

    # Soft delete
    team.is_active = False

    await db.commit()

    return {"message": "Team deleted successfully"}


# ============== Team Member Endpoints ==============


@router.post("/{event_id}/teams/{team_id}/members", response_model=TeamMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_team_member(
    event_id: int,
    team_id: int,
    member_data: TeamMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Add a member to a team.
    Only team captain or event owner can add members.
    """
    query = (
        select(Team)
        .options(
            selectinload(Team.event),
            selectinload(Team.members),
        )
        .where(Team.id == team_id, Team.event_id == event_id, Team.is_active == True)
    )
    result = await db.execute(query)
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_event_owner = team.event.created_by_id == current_user.id
    is_captain = team.created_by_id == current_user.id

    if not (is_admin or is_event_owner or is_captain):
        raise HTTPException(
            status_code=403,
            detail="Only team captain or event owner can add members"
        )

    # Verify enrollment exists and is approved
    enrollment_query = (
        select(EventEnrollment)
        .options(selectinload(EventEnrollment.user).selectinload(UserAccount.profile))
        .where(
            EventEnrollment.id == member_data.enrollment_id,
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(
            status_code=400,
            detail="Enrollment not found or not approved"
        )

    # Check if already in a team
    existing_query = (
        select(TeamMember)
        .join(Team)
        .where(
            Team.event_id == event_id,
            TeamMember.enrollment_id == member_data.enrollment_id,
            TeamMember.is_active == True,
        )
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="User is already in a team for this event"
        )

    # Check team size limit
    if team.event.max_team_size:
        active_members = sum(1 for m in team.members if m.is_active)
        if active_members >= team.event.max_team_size:
            raise HTTPException(
                status_code=400,
                detail=f"Team has reached maximum size ({team.event.max_team_size})"
            )

    # Add member
    member = TeamMember(
        team_id=team_id,
        enrollment_id=member_data.enrollment_id,
        role=member_data.role or TeamMemberRole.MEMBER.value,
        added_by_id=current_user.id,
    )
    db.add(member)
    await db.commit()

    # Reload with relationships
    member_query = (
        select(TeamMember)
        .options(
            selectinload(TeamMember.enrollment).selectinload(EventEnrollment.user).selectinload(UserAccount.profile)
        )
        .where(TeamMember.id == member.id)
    )
    member_result = await db.execute(member_query)
    member = member_result.scalar_one()

    return member_to_response(member)


@router.delete("/{event_id}/teams/{team_id}/members/{member_id}", response_model=MessageResponse)
async def remove_team_member(
    event_id: int,
    team_id: int,
    member_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Remove a member from a team.
    Only team captain, event owner, or the member themselves can remove.
    """
    query = (
        select(TeamMember)
        .options(
            selectinload(TeamMember.team).selectinload(Team.event),
            selectinload(TeamMember.enrollment),
        )
        .where(
            TeamMember.id == member_id,
            TeamMember.team_id == team_id,
            TeamMember.is_active == True,
        )
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    if member.team.event_id != event_id:
        raise HTTPException(status_code=404, detail="Team member not found")

    # Check permissions
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles
    is_event_owner = member.team.event.created_by_id == current_user.id
    is_captain = member.team.created_by_id == current_user.id
    is_self = member.enrollment.user_id == current_user.id

    if not (is_admin or is_event_owner or is_captain or is_self):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to remove this member"
        )

    # Cannot remove captain (they must delete the team instead)
    if member.role == TeamMemberRole.CAPTAIN.value and not (is_admin or is_event_owner):
        raise HTTPException(
            status_code=400,
            detail="Team captain cannot be removed. Delete the team instead."
        )

    # Soft delete
    member.is_active = False

    await db.commit()

    return {"message": "Team member removed successfully"}


@router.get("/{event_id}/my-team", response_model=TeamDetailResponse)
async def get_my_team(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """Get current user's team for an event."""
    # Get user's enrollment
    enrollment_query = select(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.user_id == current_user.id,
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Not enrolled in this event")

    # Get team membership
    member_query = (
        select(TeamMember)
        .options(
            selectinload(TeamMember.team)
            .selectinload(Team.created_by)
            .selectinload(UserAccount.profile),
            selectinload(TeamMember.team).selectinload(Team.members).selectinload(TeamMember.enrollment).selectinload(EventEnrollment.user).selectinload(UserAccount.profile),
        )
        .where(
            TeamMember.enrollment_id == enrollment.id,
            TeamMember.is_active == True,
        )
    )
    member_result = await db.execute(member_query)
    membership = member_result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Not in a team for this event")

    team = membership.team
    response = team_to_response(team)
    response["members"] = [member_to_response(m) for m in team.members if m.is_active]

    return response
