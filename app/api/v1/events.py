"""Event management endpoints."""

from typing import Optional
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status as http_status, UploadFile, File
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional
from app.core.permissions import OrganizerOrAdmin, ValidatorOrAdmin, AdminOnly, EventOwnerOrAdmin
from app.models.user import UserAccount, UserProfile
from app.models.event import Event, EventType, ScoringConfig, EventPrize, EventScoringRule, EventFishScoring, EventSpeciesBonusPoints, EventStatus
from app.models.event_validator import EventValidator
from app.models.club import Club, ClubMembership, MembershipStatus
from app.models.fish import Fish
from app.models.enrollment import EventEnrollment
from app.models.admin import AdminActionLog, AdminActionType
from app.models.event_sponsor import EventSponsor
from app.models.sponsor import Sponsor, SponsorTier, TIER_ORDER
from app.models.rules import OrganizerRule, OrganizerRuleDefault
from app.schemas.event import (
    EventCreate,
    EventUpdate,
    EventResponse,
    EventListResponse,
    EventTypeResponse,
    ScoringConfigResponse,
    EventFishScoringCreate,
    EventFishScoringUpdate,
    EventFishScoringResponse,
    EventSpeciesBonusPointsCreate,
    EventSpeciesBonusPointsResponse,
    ForceStatusChangeRequest,
    EventStatusUpdateRequest,
    EventStatusUpdateResponse,
    PublishReadinessResponse,
)
from app.services.event_status import EventStatusService
from app.schemas.prize import (
    PrizeCreate,
    PrizeUpdate,
    PrizeResponse,
    PrizeBulkUpdate,
    PrizeListResponse,
)
from app.schemas.common import PaginatedResponse
from app.core.storage import storage_service

router = APIRouter()


def generate_slug(name: str, event_type: str, date: datetime) -> str:
    """Generate a URL-friendly slug for an event."""
    # Clean the name
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')

    # Add type and date
    date_str = date.strftime('%Y-%m-%d')
    return f"{slug}-{event_type}-{date_str}"


@router.get("/types", response_model=list[EventTypeResponse])
async def list_event_types(
    db: AsyncSession = Depends(get_db),
) -> list[EventType]:
    """List all active event types."""
    query = select(EventType).where(EventType.is_active == True).order_by(EventType.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/types/my-accessible")
async def get_my_accessible_event_types(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
):
    """
    Get event types the current user can create.

    Returns only event types the user has been granted access to,
    along with whether they can create national events.
    """
    from app.services.organizer_permissions import OrganizerPermissionService

    permission_service = OrganizerPermissionService(db)
    summary = await permission_service.get_user_permissions_summary(current_user.id)

    return {
        "event_types": [
            {
                "id": et.id,
                "name": et.name,
                "code": et.code,
                "format_code": et.format_code,
                "description": et.description,
                "icon_url": et.icon_url,
                "is_active": et.is_active,
            }
            for et in summary["event_types"]
        ],
        "can_create_national": summary["can_create_national"],
    }


@router.get("/scoring-configs", response_model=list[ScoringConfigResponse])
async def list_scoring_configs(
    event_type_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
) -> list[ScoringConfig]:
    """List scoring configurations, optionally filtered by event type."""
    from app.models.event import scoring_config_event_types

    query = (
        select(ScoringConfig)
        .options(selectinload(ScoringConfig.event_types))
        .where(ScoringConfig.is_active == True)
    )
    if event_type_id:
        # Filter by event type through the M2M relationship
        query = query.join(scoring_config_event_types).where(
            scoring_config_event_types.c.event_type_id == event_type_id
        )
    query = query.order_by(ScoringConfig.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("", response_model=PaginatedResponse[EventListResponse])
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[str] = None,
    event_type_id: Optional[int] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
) -> dict:
    """
    List events with pagination and filters.
    Public endpoint - shows published events to all, draft events only to organizers.
    Use status=deleted to see deleted events (organizers/admins only).
    """
    query = (
        select(Event)
        .options(selectinload(Event.event_type))
    )

    # Handle deleted filter specially
    if status == "deleted":
        # Only organizers/admins can see deleted events
        if current_user is None:
            raise HTTPException(
                status_code=http_http_status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to view deleted events",
            )
        user_roles = current_user.profile.roles if current_user.profile else []
        if "administrator" not in user_roles and "organizer" not in user_roles:
            raise HTTPException(
                status_code=http_http_status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view deleted events",
            )
        query = query.where(Event.is_deleted == True)
        # Organizers only see their own deleted events, admins see all
        if "administrator" not in user_roles:
            query = query.where(Event.created_by_id == current_user.id)
    else:
        # Normal query - exclude deleted events
        query = query.where(Event.is_deleted == False)
        # Filter by status
        if status:
            query = query.where(Event.status == status)
        else:
            # Check if user is admin - admins see all events including drafts
            is_admin = (
                current_user
                and current_user.profile
                and "administrator" in current_user.profile.roles
            )
            if not is_admin:
                # Regular users only see published/ongoing/completed events
                # Draft events are only visible via /events/organized endpoint
                query = query.where(Event.status.in_([
                    EventStatus.PUBLISHED.value,
                    EventStatus.ONGOING.value,
                    EventStatus.COMPLETED.value,
                ]))

    # Filter by event type
    if event_type_id:
        query = query.where(Event.event_type_id == event_type_id)

    # Search by name
    if search:
        query = query.where(Event.name.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(Event.start_date.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    events = result.scalars().all()

    # Get enrollment counts for all events in one query
    if events:
        event_ids = [e.id for e in events]
        enrollment_counts_query = (
            select(
                EventEnrollment.event_id,
                func.count().label('enrolled_count')
            )
            .where(EventEnrollment.event_id.in_(event_ids))
            .group_by(EventEnrollment.event_id)
        )
        counts_result = await db.execute(enrollment_counts_query)
        counts_map = {row.event_id: row.enrolled_count for row in counts_result}

        # Build response with enrollment counts
        items = []
        for event in events:
            event_dict = {
                "id": event.id,
                "name": event.name,
                "slug": event.slug,
                "event_type": event.event_type,
                "start_date": event.start_date,
                "end_date": event.end_date,
                "status": event.status,
                "location_name": event.location_name,
                "image_url": event.image_url,
                "is_team_event": event.is_team_event,
                "is_national_event": event.is_national_event,
                "is_tournament_event": event.is_tournament_event,
                "enrolled_count": counts_map.get(event.id, 0),
                "max_participants": event.max_participants,
            }
            items.append(event_dict)
    else:
        items = []

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


# ============== Role-Filtered Event Endpoints ==============
# NOTE: These must be defined BEFORE /{event_id} to prevent route conflicts


@router.get("/organized", response_model=PaginatedResponse[EventListResponse])
async def list_my_organized_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> dict:
    """
    List events created by the current user.
    Only organizers and administrators can access this endpoint.
    Organizers see only their own events, admins see all events.
    Use status=deleted to see deleted events.
    Use search to filter by event name.
    """
    query = (
        select(Event)
        .options(selectinload(Event.event_type))
    )

    # Organizers see only their events, admins see all
    user_roles = current_user.profile.roles if current_user.profile else []
    if "administrator" not in user_roles:
        query = query.where(Event.created_by_id == current_user.id)

    # Handle deleted filter specially
    if status_filter == "deleted":
        query = query.where(Event.is_deleted == True)
    else:
        query = query.where(Event.is_deleted == False)
        if status_filter:
            query = query.where(Event.status == status_filter)

    # Search by name
    if search:
        query = query.where(Event.name.ilike(f"%{search}%"))

    # Get total count
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(Event.start_date.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    events = result.scalars().all()

    return PaginatedResponse.create(
        items=events,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/validate", response_model=PaginatedResponse[EventListResponse])
async def list_my_validation_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(ValidatorOrAdmin),
) -> dict:
    """
    List events where current user is assigned as validator.
    Only validators and administrators can access this endpoint.
    """
    user_roles = current_user.profile.roles if current_user.profile else []

    # Admins see all events, validators see only assigned events
    if "administrator" in user_roles:
        query = select(Event).options(selectinload(Event.event_type)).where(Event.is_deleted == False)
    else:
        # Get events where user is an active validator
        query = (
            select(Event)
            .options(selectinload(Event.event_type))
            .join(EventValidator, Event.id == EventValidator.event_id)
            .where(
                EventValidator.validator_id == current_user.id,
                EventValidator.is_active == True,
                Event.is_deleted == False
            )
        )

    if status_filter:
        query = query.where(Event.status == status_filter)

    # Get total count
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.order_by(Event.start_date.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    events = result.scalars().all()

    return PaginatedResponse.create(
        items=events,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/my-events")
async def list_my_enrolled_events(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    List events the current user is enrolled in, grouped by status.
    Returns ongoing, upcoming (published), and completed events.
    Only includes approved or pending enrollments.
    """
    from app.models.enrollment import EventEnrollment, EnrollmentStatus

    # Get events where user has approved or pending enrollment
    enrolled_event_ids_query = (
        select(EventEnrollment.event_id)
        .where(
            EventEnrollment.user_id == current_user.id,
            EventEnrollment.status.in_([
                EnrollmentStatus.APPROVED.value,
                EnrollmentStatus.PENDING.value,
            ]),
        )
    )
    enrolled_result = await db.execute(enrolled_event_ids_query)
    enrolled_event_ids = [row[0] for row in enrolled_result.all()]

    if not enrolled_event_ids:
        return {
            "ongoing": [],
            "upcoming": [],
            "completed": [],
        }

    # Get ongoing (live) events
    ongoing_query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(
            Event.id.in_(enrolled_event_ids),
            Event.status == EventStatus.ONGOING.value,
            Event.is_deleted == False,
        )
        .order_by(Event.start_date.asc())
    )
    ongoing_result = await db.execute(ongoing_query)
    ongoing_events = ongoing_result.scalars().all()

    # Get upcoming (published) events
    upcoming_query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(
            Event.id.in_(enrolled_event_ids),
            Event.status == EventStatus.PUBLISHED.value,
            Event.is_deleted == False,
        )
        .order_by(Event.start_date.asc())
    )
    upcoming_result = await db.execute(upcoming_query)
    upcoming_events = upcoming_result.scalars().all()

    # Get completed events (most recent first)
    completed_query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(
            Event.id.in_(enrolled_event_ids),
            Event.status == EventStatus.COMPLETED.value,
            Event.is_deleted == False,
        )
        .order_by(Event.end_date.desc())
        .limit(50)  # Limit to recent 50 completed events
    )
    completed_result = await db.execute(completed_query)
    completed_events = completed_result.scalars().all()

    # Convert to response format
    def event_to_dict(event: Event) -> dict:
        return {
            "id": event.id,
            "name": event.name,
            "slug": event.slug,
            "description": event.description,
            "event_type_id": event.event_type_id,
            "event_type": {
                "id": event.event_type.id,
                "name": event.event_type.name,
                "code": event.event_type.code,
            } if event.event_type else None,
            "start_date": event.start_date.isoformat() if event.start_date else None,
            "end_date": event.end_date.isoformat() if event.end_date else None,
            "registration_deadline": event.registration_deadline.isoformat() if event.registration_deadline else None,
            "location_name": event.location_name,
            "max_participants": event.max_participants,
            "status": event.status,
            "image_url": event.image_url,
            "is_team_event": event.is_team_event,
            "is_national_event": event.is_national_event,
        }

    return {
        "ongoing": [event_to_dict(e) for e in ongoing_events],
        "upcoming": [event_to_dict(e) for e in upcoming_events],
        "completed": [event_to_dict(e) for e in completed_events],
    }


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
) -> dict:
    """Get event by ID."""
    from app.models.location import FishingSpot, City, Country
    query = (
        select(Event)
        .options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config).selectinload(ScoringConfig.event_types),
            selectinload(Event.created_by).selectinload(UserAccount.profile),
            selectinload(Event.rule),
            selectinload(Event.location).selectinload(FishingSpot.city).selectinload(City.country),
        )
        .where(Event.id == event_id, Event.is_deleted == False)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check access for draft events
    if event.status == EventStatus.DRAFT.value:
        if not current_user or (
            event.created_by_id != current_user.id
            and not current_user.profile.has_any_role("administrator")
        ):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Event not found",
            )

    # Build location response with city and country
    location_response = None
    if event.location:
        city_response = None
        if event.location.city:
            country_response = None
            if event.location.city.country:
                country_response = {
                    "id": event.location.city.country.id,
                    "name": event.location.city.country.name,
                    "code": event.location.city.country.code,
                }
            city_response = {
                "id": event.location.city.id,
                "name": event.location.city.name,
                "country": country_response,
            }
        location_response = {
            "id": event.location.id,
            "name": event.location.name,
            "city": city_response,
            "latitude": event.location.latitude,
            "longitude": event.location.longitude,
        }

    # Fetch sponsors separately (dynamic relationship)
    sponsors_query = (
        select(EventSponsor)
        .options(selectinload(EventSponsor.sponsor))
        .where(EventSponsor.event_id == event_id)
        .order_by(EventSponsor.display_order)
    )
    sponsors_result = await db.execute(sponsors_query)
    event_sponsors = sponsors_result.scalars().all()

    # Build sponsors list sorted by tier priority
    sponsors_list = []
    if event_sponsors:
        sorted_sponsors = sorted(
            [es for es in event_sponsors if es.sponsor and es.sponsor.is_active],
            key=lambda es: (TIER_ORDER.get(SponsorTier(es.sponsor.tier) if es.sponsor.tier else SponsorTier.PARTNER, 99), es.display_order)
        )
        sponsors_list = [
            {
                "id": es.sponsor.id,
                "name": es.sponsor.name,
                "logo_url": es.sponsor.logo_url,
                "website_url": es.sponsor.website_url,
                "tier": es.sponsor.tier,
            }
            for es in sorted_sponsors
        ]

    # Fetch organizer's club (if they own one)
    organizer_club_name = None
    organizer_club_logo_url = None
    if event.created_by_id:
        from app.models.club import Club
        club_query = select(Club).where(
            Club.owner_id == event.created_by_id,
            Club.is_deleted == False,
            Club.is_active == True,
        )
        club_result = await db.execute(club_query)
        club = club_result.scalar_one_or_none()
        if club:
            organizer_club_name = club.name
            organizer_club_logo_url = club.logo_url

    # Fetch fish_scoring separately (dynamic relationship)
    fish_scoring_query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(EventFishScoring.event_id == event_id)
        .order_by(EventFishScoring.display_order)
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring_records = fish_scoring_result.scalars().all()

    # Build fish_scoring list with all required fields for schema
    fish_scoring_list = [
        {
            "id": fs.id,
            "event_id": fs.event_id,
            "fish_id": fs.fish_id,
            "fish": {
                "id": fs.fish.id,
                "name": fs.fish.name,
                "slug": fs.fish.slug,
                "name_en": getattr(fs.fish, 'name_en', None),
                "name_ro": getattr(fs.fish, 'name_ro', None),
                "scientific_name": fs.fish.scientific_name,
                "min_length": getattr(fs.fish, 'min_length', None),
                "max_length": getattr(fs.fish, 'max_length', None),
                "image_url": fs.fish.image_url,
            } if fs.fish else None,
            "accountable_catch_slots": fs.accountable_catch_slots,
            "accountable_min_length": fs.accountable_min_length,
            "under_min_length_points": fs.under_min_length_points,
            "top_x_catches": fs.top_x_catches,
            "display_order": fs.display_order,
            "created_at": fs.created_at,
            "updated_at": fs.updated_at,
        }
        for fs in fish_scoring_records
    ]

    # Count enrollments for this event
    enrolled_count_query = select(func.count()).select_from(EventEnrollment).where(
        EventEnrollment.event_id == event_id
    )
    enrolled_result = await db.execute(enrolled_count_query)
    enrolled_count = enrolled_result.scalar() or 0

    # Count approved enrollments
    approved_count_query = select(func.count()).select_from(EventEnrollment).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == "approved"
    )
    approved_result = await db.execute(approved_count_query)
    approved_count = approved_result.scalar() or 0

    # Build response with organizer info, rule, location and sponsors
    response = {
        **{k: v for k, v in event.__dict__.items() if not k.startswith('_') and k != 'fish_scoring'},
        "enrolled_count": enrolled_count,
        "approved_count": approved_count,
        "organizer": {
            "id": event.created_by.id,
            "email": event.created_by.email,
            "first_name": event.created_by.profile.first_name if event.created_by.profile else None,
            "last_name": event.created_by.profile.last_name if event.created_by.profile else None,
        } if event.created_by else None,
        "organizer_club_name": organizer_club_name,
        "organizer_club_logo_url": organizer_club_logo_url,
        "rule": {
            "id": event.rule.id,
            "name": event.rule.name,
            "content": event.rule.content,
            "external_url": event.rule.external_url,
            "document_url": event.rule.document_url,
        } if event.rule else None,
        "location": location_response,
        "sponsors": sponsors_list,
        "fish_scoring": fish_scoring_list,
    }

    return response


@router.get("/by-slug/{slug}", response_model=EventResponse)
async def get_event_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[UserAccount] = Depends(get_current_user_optional),
) -> dict:
    """Get event by slug (for public shareable URLs)."""
    from app.models.location import FishingSpot, City, Country
    query = (
        select(Event)
        .options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config).selectinload(ScoringConfig.event_types),
            selectinload(Event.created_by).selectinload(UserAccount.profile),
            selectinload(Event.rule),
            selectinload(Event.location).selectinload(FishingSpot.city).selectinload(City.country),
        )
        .where(Event.slug == slug, Event.is_deleted == False)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check access for draft events
    if event.status == EventStatus.DRAFT.value:
        if not current_user or (
            event.created_by_id != current_user.id
            and not current_user.profile.has_any_role("administrator")
        ):
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Event not found",
            )

    # Build location response with city and country
    location_response = None
    if event.location:
        city_response = None
        if event.location.city:
            country_response = None
            if event.location.city.country:
                country_response = {
                    "id": event.location.city.country.id,
                    "name": event.location.city.country.name,
                    "code": event.location.city.country.code,
                }
            city_response = {
                "id": event.location.city.id,
                "name": event.location.city.name,
                "country": country_response,
            }
        location_response = {
            "id": event.location.id,
            "name": event.location.name,
            "city": city_response,
            "latitude": event.location.latitude,
            "longitude": event.location.longitude,
        }

    # Fetch sponsors separately (dynamic relationship)
    sponsors_query = (
        select(EventSponsor)
        .options(selectinload(EventSponsor.sponsor))
        .where(EventSponsor.event_id == event.id)
        .order_by(EventSponsor.display_order)
    )
    sponsors_result = await db.execute(sponsors_query)
    event_sponsors = sponsors_result.scalars().all()

    # Build sponsors list sorted by tier priority
    sponsors_list = []
    if event_sponsors:
        sorted_sponsors = sorted(
            [es for es in event_sponsors if es.sponsor and es.sponsor.is_active],
            key=lambda es: (TIER_ORDER.get(SponsorTier(es.sponsor.tier) if es.sponsor.tier else SponsorTier.PARTNER, 99), es.display_order)
        )
        sponsors_list = [
            {
                "id": es.sponsor.id,
                "name": es.sponsor.name,
                "logo_url": es.sponsor.logo_url,
                "website_url": es.sponsor.website_url,
                "tier": es.sponsor.tier,
            }
            for es in sorted_sponsors
        ]

    # Fetch organizer's club (if they own one)
    organizer_club_name = None
    organizer_club_logo_url = None
    if event.created_by_id:
        from app.models.club import Club
        club_query = select(Club).where(
            Club.owner_id == event.created_by_id,
            Club.is_deleted == False,
            Club.is_active == True,
        )
        club_result = await db.execute(club_query)
        club = club_result.scalar_one_or_none()
        if club:
            organizer_club_name = club.name
            organizer_club_logo_url = club.logo_url

    # Fetch fish_scoring separately (dynamic relationship)
    fish_scoring_query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(EventFishScoring.event_id == event.id)
        .order_by(EventFishScoring.display_order)
    )
    fish_scoring_result = await db.execute(fish_scoring_query)
    fish_scoring_records = fish_scoring_result.scalars().all()

    # Build fish_scoring list with all required fields for schema
    fish_scoring_list = [
        {
            "id": fs.id,
            "event_id": fs.event_id,
            "fish_id": fs.fish_id,
            "fish": {
                "id": fs.fish.id,
                "name": fs.fish.name,
                "slug": fs.fish.slug,
                "name_en": getattr(fs.fish, 'name_en', None),
                "name_ro": getattr(fs.fish, 'name_ro', None),
                "scientific_name": fs.fish.scientific_name,
                "min_length": getattr(fs.fish, 'min_length', None),
                "max_length": getattr(fs.fish, 'max_length', None),
                "image_url": fs.fish.image_url,
            } if fs.fish else None,
            "accountable_catch_slots": fs.accountable_catch_slots,
            "accountable_min_length": fs.accountable_min_length,
            "under_min_length_points": fs.under_min_length_points,
            "top_x_catches": fs.top_x_catches,
            "display_order": fs.display_order,
            "created_at": fs.created_at,
            "updated_at": fs.updated_at,
        }
        for fs in fish_scoring_records
    ]

    # Count enrollments for this event
    enrolled_count_query = select(func.count()).select_from(EventEnrollment).where(
        EventEnrollment.event_id == event.id
    )
    enrolled_result = await db.execute(enrolled_count_query)
    enrolled_count = enrolled_result.scalar() or 0

    # Count approved enrollments
    approved_count_query = select(func.count()).select_from(EventEnrollment).where(
        EventEnrollment.event_id == event.id,
        EventEnrollment.status == "approved"
    )
    approved_result = await db.execute(approved_count_query)
    approved_count = approved_result.scalar() or 0

    # Build response with organizer info, rule, location and sponsors
    response = {
        **{k: v for k, v in event.__dict__.items() if not k.startswith('_') and k != 'fish_scoring'},
        "enrolled_count": enrolled_count,
        "approved_count": approved_count,
        "organizer": {
            "id": event.created_by.id,
            "email": event.created_by.email,
            "first_name": event.created_by.profile.first_name if event.created_by.profile else None,
            "last_name": event.created_by.profile.last_name if event.created_by.profile else None,
        } if event.created_by else None,
        "organizer_club_name": organizer_club_name,
        "organizer_club_logo_url": organizer_club_logo_url,
        "rule": {
            "id": event.rule.id,
            "name": event.rule.name,
            "content": event.rule.content,
            "external_url": event.rule.external_url,
            "document_url": event.rule.document_url,
        } if event.rule else None,
        "location": location_response,
        "sponsors": sponsors_list,
        "fish_scoring": fish_scoring_list,
    }

    return response


@router.post("", response_model=EventResponse, status_code=http_status.HTTP_201_CREATED)
async def create_event(
    event_data: EventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    Create a new event.
    Requires organizer or administrator role.
    Organizers must own a club to create events.
    """
    # Check if user is admin (admins bypass club ownership requirement)
    user_roles = current_user.profile.roles if current_user.profile else []
    is_admin = "administrator" in user_roles

    if not is_admin:
        # Verify organizer owns a club
        club_query = select(Club).where(
            Club.owner_id == current_user.id,
            Club.is_deleted == False,
            Club.is_active == True
        )
        club_result = await db.execute(club_query)
        organizer_club = club_result.scalar_one_or_none()

        if not organizer_club:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Organizer must own a club to create events"
            )

    # Verify event type exists
    event_type_query = select(EventType).where(EventType.id == event_data.event_type_id)
    event_type_result = await db.execute(event_type_query)
    event_type = event_type_result.scalar_one_or_none()
    if not event_type:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid event type",
        )

    # Check organizer permissions (admins bypass permission checks)
    if not is_admin:
        from app.services.organizer_permissions import OrganizerPermissionService
        permission_service = OrganizerPermissionService(db)

        # Check event type access
        has_event_type_access = await permission_service.check_event_type_access(
            current_user.id, event_data.event_type_id
        )
        if not has_event_type_access:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"You don't have permission to create {event_type.name} events. Contact the platform administrator for access.",
            )

        # Check national event permission if needed
        if event_data.is_national_event:
            has_national_permission = await permission_service.check_national_permission(current_user.id)
            if not has_national_permission:
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to create national events. Contact the platform administrator for access.",
                )

    # Verify scoring config exists
    scoring_config_query = select(ScoringConfig).where(
        ScoringConfig.id == event_data.scoring_config_id
    )
    scoring_config_result = await db.execute(scoring_config_query)
    scoring_config = scoring_config_result.scalar_one_or_none()
    if not scoring_config:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Invalid scoring configuration",
        )

    # Validate top_x_overall is required for "Top X Overall" scoring types
    is_top_x_overall_scoring = (
        "top_x_overall" in scoring_config.code or "top_n_overall" in scoring_config.code
    )
    if is_top_x_overall_scoring and not event_data.top_x_overall:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Top X value is required for this scoring type",
        )

    # Determine rule_id (use provided or auto-apply default)
    rule_id = event_data.rule_id
    if not rule_id:
        # Check for default rule for this event type
        default_query = select(OrganizerRuleDefault).where(
            OrganizerRuleDefault.owner_id == current_user.id,
            OrganizerRuleDefault.event_type_id == event_data.event_type_id,
        )
        default_result = await db.execute(default_query)
        default = default_result.scalar_one_or_none()
        if default:
            rule_id = default.rule_id

    # If rule_id provided/found, verify it exists and belongs to user
    if rule_id:
        rule_query = select(OrganizerRule).where(
            OrganizerRule.id == rule_id,
            OrganizerRule.owner_id == current_user.id,
            OrganizerRule.is_active == True,
        )
        rule_result = await db.execute(rule_query)
        if not rule_result.scalar_one_or_none():
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid rule or not authorized to use this rule",
            )

    # Generate slug
    slug = generate_slug(event_data.name, event_type.code, event_data.start_date)

    # Check slug uniqueness
    slug_query = select(Event).where(Event.slug == slug)
    slug_result = await db.execute(slug_query)
    if slug_result.scalar_one_or_none():
        # Add timestamp to make unique
        slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp())}"

    # Create event
    event = Event(
        name=event_data.name,
        slug=slug,
        description=event_data.description,
        event_type_id=event_data.event_type_id,
        scoring_config_id=event_data.scoring_config_id,
        start_date=event_data.start_date,
        end_date=event_data.end_date,
        registration_deadline=event_data.registration_deadline,
        location_id=event_data.location_id,
        location_name=event_data.location_name,
        max_participants=event_data.max_participants,
        requires_approval=event_data.requires_approval,
        top_x_overall=event_data.top_x_overall,
        has_bonus_points=event_data.has_bonus_points,
        is_team_event=event_data.is_team_event,
        is_national_event=event_data.is_national_event,
        is_tournament_event=event_data.is_tournament_event,
        min_team_size=event_data.min_team_size,
        max_team_size=event_data.max_team_size,
        rule_id=rule_id,
        rules=event_data.rules,
        allow_gallery_upload=event_data.allow_gallery_upload,
        allowed_media_type=event_data.allowed_media_type,
        max_video_duration=event_data.max_video_duration,
        created_by_id=current_user.id,
        status=EventStatus.DRAFT.value,
    )
    db.add(event)
    await db.flush()

    # Add prizes if provided
    if event_data.prizes:
        for prize_data in event_data.prizes:
            prize = EventPrize(
                event_id=event.id,
                place=prize_data.place,
                title=prize_data.title,
                description=prize_data.description,
                value=prize_data.value,
            )
            db.add(prize)

    # Add scoring rules if provided
    if event_data.scoring_rules:
        for rule_data in event_data.scoring_rules:
            rule = EventScoringRule(
                event_id=event.id,
                fish_id=rule_data.fish_id,
                min_length=rule_data.min_length,
                max_length=rule_data.max_length,
                points_per_cm=rule_data.points_per_cm,
                bonus_points=rule_data.bonus_points,
                points_formula=rule_data.points_formula,
            )
            db.add(rule)

    # Add fish scoring configurations if provided
    if event_data.fish_scoring:
        for idx, fish_scoring_data in enumerate(event_data.fish_scoring):
            # Verify fish exists
            fish_query = select(Fish).where(Fish.id == fish_scoring_data.fish_id)
            fish_result = await db.execute(fish_query)
            if fish_result.scalar_one_or_none():
                fish_scoring = EventFishScoring(
                    event_id=event.id,
                    fish_id=fish_scoring_data.fish_id,
                    accountable_catch_slots=fish_scoring_data.accountable_catch_slots,
                    accountable_min_length=fish_scoring_data.accountable_min_length,
                    under_min_length_points=fish_scoring_data.under_min_length_points,
                    top_x_catches=fish_scoring_data.top_x_catches,
                    display_order=fish_scoring_data.display_order if fish_scoring_data.display_order else idx,
                )
                db.add(fish_scoring)

    # Add species bonus points if provided
    if event_data.bonus_points and event_data.has_bonus_points:
        for bonus_data in event_data.bonus_points:
            bonus = EventSpeciesBonusPoints(
                event_id=event.id,
                species_count=bonus_data.species_count,
                bonus_points=bonus_data.bonus_points,
            )
            db.add(bonus)

    await db.commit()
    await db.refresh(event)

    # Load relationships needed for EventResponse
    await db.refresh(event, ["event_type", "scoring_config", "fish_scoring"])

    return event


@router.patch("/{event_id}", response_model=EventListResponse)
async def update_event(
    event_id: int,
    event_data: EventUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    Update an event.
    Only the event creator or administrators can update.
    """
    query = (
        select(Event)
        .options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config),
        )
        .where(Event.id == event_id)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check ownership (unless admin)
    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this event",
        )

    # Block all changes for completed/cancelled events
    if event.status in [EventStatus.COMPLETED.value, EventStatus.CANCELLED.value]:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot modify {event.status} events",
        )

    # Block scoring field changes for non-draft events
    if event.status != EventStatus.DRAFT.value:
        if event_data.event_type_id is not None and event_data.event_type_id != event.event_type_id:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Cannot change event type for non-draft events",
            )
        if event_data.scoring_config_id is not None and event_data.scoring_config_id != event.scoring_config_id:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Cannot change scoring configuration for non-draft events",
            )

    # Validate rule_id if provided and changed
    if event_data.rule_id is not None and event_data.rule_id != event.rule_id:
        if event_data.rule_id > 0:
            # Admins can use any active rule, organizers can only use their own
            is_admin = current_user.profile.has_role("administrator")
            rule_query = select(OrganizerRule).where(
                OrganizerRule.id == event_data.rule_id,
                OrganizerRule.is_active == True,
            )
            if not is_admin:
                rule_query = rule_query.where(OrganizerRule.owner_id == current_user.id)
            rule_result = await db.execute(rule_query)
            if not rule_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail="Invalid rule or not authorized to use this rule",
                )

    # Validate top_x_overall for "Top X Overall" scoring types
    if event.scoring_config:
        is_top_x_overall_scoring = (
            "top_x_overall" in event.scoring_config.code or "top_n_overall" in event.scoring_config.code
        )
        if is_top_x_overall_scoring:
            # Check if trying to clear top_x_overall or if it would result in NULL
            new_top_x = event_data.top_x_overall if event_data.top_x_overall is not None else event.top_x_overall
            if not new_top_x:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail="Top X value is required for this scoring type",
                )

    # Apply updates
    update_data = event_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "status" and value:
            value = value.value  # Convert enum to string
        setattr(event, field, value)

    # Update published_at if publishing
    if event_data.status == EventStatus.PUBLISHED and event.published_at is None:
        event.published_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event)

    return event


@router.post("/{event_id}/publish", response_model=EventResponse, deprecated=True)
async def publish_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="publish" instead.

    Publish a draft event.
    """
    query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to publish this event",
        )

    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Only draft events can be published",
        )

    event.status = EventStatus.PUBLISHED.value
    event.published_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(event, ["event_type", "scoring_config"])

    return event


@router.post("/{event_id}/recall", response_model=EventResponse, deprecated=True)
async def recall_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="recall" instead.

    Recall a published event back to draft status.
    Cannot recall ongoing or completed events.
    """
    query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to recall this event",
        )

    if event.status == EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Cannot recall an ongoing event",
        )

    if event.status == EventStatus.COMPLETED.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Cannot recall a completed event",
        )

    if event.status == EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Event is already in draft status",
        )

    event.status = EventStatus.DRAFT.value

    await db.commit()
    await db.refresh(event, ["event_type", "scoring_config"])

    return event


@router.get("/{event_id}/publish-readiness", response_model=PublishReadinessResponse)
async def get_publish_readiness(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(EventOwnerOrAdmin),
) -> dict:
    """
    Check if an event is ready to be published.

    Returns validation results including:
    - is_ready: True if all validations pass
    - missing_items: List of i18n message keys for failed validations
    - checks: Individual check results (check_name -> pass/fail)

    Common checks (all event types):
    - name: Event has a name
    - location: Event has a location
    - start_date: Event has a start date in the future
    - end_date: Event has an end date after start date

    SF-specific checks:
    - sf_has_species: At least one allowed species configured
    - sf_has_scoring_config: Scoring configuration selected

    TA-specific checks:
    - ta_has_settings: TA settings exist
    - ta_has_legs: Number of legs configured

    TSF-specific checks:
    - tsf_has_settings: TSF settings exist
    - tsf_has_days: Number of days configured
    - tsf_has_sectors: Number of sectors configured
    """
    from app.services.publish_validation import PublishValidationService

    service = PublishValidationService(db)
    is_ready, missing_items, checks = await service.validate_publish_readiness(event_id)

    return {
        "is_ready": is_ready,
        "missing_items": missing_items,
        "checks": checks,
    }


@router.patch("/{event_id}/status", response_model=EventStatusUpdateResponse)
async def update_event_status(
    event_id: int,
    request: EventStatusUpdateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> dict:
    """
    Unified endpoint for all event status transitions.

    Actions:
    - publish: DRAFT -> PUBLISHED
    - recall: PUBLISHED -> DRAFT
    - start: PUBLISHED -> ONGOING (requires approved participants)
    - stop: ONGOING -> COMPLETED
    - cancel: ANY -> CANCELLED (requires reason)
    - delete: Soft-delete the event
    - restore: Restore a soft-deleted event to DRAFT

    Set `force=true` with a `reason` to bypass transition rules (owner/admin only).
    """
    service = EventStatusService(db)
    event, previous_status, warnings = await service.update_status(
        event_id=event_id,
        action=request.action,
        user=current_user,
        reason=request.reason,
        force=request.force,
    )

    # Trigger background tasks based on action
    if request.action == "publish":
        # Notify users about the new event based on their preferences
        from app.tasks.notifications import send_new_event_notification
        # Get country_id from location if available
        country_id = None
        if event.location_id:
            from app.models.location import FishingSpot
            location_query = select(FishingSpot).where(FishingSpot.id == event.location_id)
            location_result = await db.execute(location_query)
            location = location_result.scalar_one_or_none()
            if location and location.city:
                country_id = location.city.country_id
        send_new_event_notification.delay(
            event_id=event.id,
            event_name=event.name,
            event_type_id=event.event_type_id,
            organizer_id=event.created_by_id,
            country_id=country_id,
        )
    elif request.action == "start":
        background_tasks.add_task(broadcast_event_started, event.id, event.name)
        from app.tasks.notifications import send_event_started_notifications
        send_event_started_notifications.delay(event.id, event.name)
    elif request.action == "stop":
        background_tasks.add_task(broadcast_event_stopped, event.id, event.name)
        from app.tasks.notifications import send_event_stopped_notifications
        send_event_stopped_notifications.delay(event.id, event.name)
        from app.tasks.billing import generate_event_invoice
        generate_event_invoice.delay(event_id)
        # Trigger stats recalculation for all event participants
        from app.tasks.statistics import recalculate_event_stats
        recalculate_event_stats.delay(event_id)

        # Trigger achievement processing for TA/TSF events (SF handled separately)
        from app.utils.event_formats import get_format_code
        format_code = get_format_code(event.event_type)
        if format_code in ("ta", "tsf"):
            from app.tasks.achievement_processing import process_format_event_achievements
            process_format_event_achievements.delay(event_id, format_code)

    response = {
        "id": event.id,
        "status": event.status,
        "is_deleted": event.is_deleted,
        "previous_status": previous_status,
        "action_performed": request.action,
        "published_at": event.published_at,
        "completed_at": event.completed_at,
        "deleted_at": event.deleted_at,
    }

    # Include warnings if any (non-blocking issues)
    if warnings:
        response["warnings"] = warnings

    return response


@router.delete("/{event_id}", status_code=http_status.HTTP_204_NO_CONTENT, deprecated=True)
async def delete_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> None:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="delete" instead.

    Soft delete an event.
    Cannot delete ongoing events.
    """
    query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this event",
        )

    if event.status == EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an ongoing event. Stop the event first.",
        )

    event.is_deleted = True
    event.deleted_at = datetime.now(timezone.utc)
    event.deleted_by_id = current_user.id

    await db.commit()


@router.post("/{event_id}/restore", response_model=EventResponse, deprecated=True)
async def restore_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="restore" instead.

    Restore a soft-deleted event back to draft status.
    """
    # Query including deleted events
    query = select(Event).where(Event.id == event_id, Event.is_deleted == True)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Deleted event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to restore this event",
        )

    event.is_deleted = False
    event.deleted_at = None
    event.deleted_by_id = None
    event.status = EventStatus.DRAFT.value

    await db.commit()
    await db.refresh(event, ["event_type", "scoring_config"])

    return event


# ============== Event Fish Scoring Endpoints ==============


@router.get("/{event_id}/fish-scoring", response_model=list[EventFishScoringResponse])
async def list_event_fish_scoring(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> list[EventFishScoring]:
    """Get all fish scoring configurations for an event."""
    # Verify event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Get fish scoring configs
    query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(EventFishScoring.event_id == event_id)
        .order_by(EventFishScoring.display_order, EventFishScoring.id)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/{event_id}/fish-scoring", response_model=EventFishScoringResponse, status_code=http_status.HTTP_201_CREATED)
async def add_fish_scoring(
    event_id: int,
    scoring_data: EventFishScoringCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> EventFishScoring:
    """Add a fish species scoring configuration to an event."""
    # Verify event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check ownership
    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this event",
        )

    # Can only modify draft events
    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Can only modify fish scoring for draft events",
        )

    # Verify fish exists
    fish_query = select(Fish).where(Fish.id == scoring_data.fish_id)
    fish_result = await db.execute(fish_query)
    fish = fish_result.scalar_one_or_none()
    if not fish:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Fish species not found",
        )

    # Check if fish is already added to this event
    existing_query = select(EventFishScoring).where(
        EventFishScoring.event_id == event_id,
        EventFishScoring.fish_id == scoring_data.fish_id,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Fish species already added to this event",
        )

    # Create fish scoring
    fish_scoring = EventFishScoring(
        event_id=event_id,
        fish_id=scoring_data.fish_id,
        accountable_catch_slots=scoring_data.accountable_catch_slots,
        accountable_min_length=scoring_data.accountable_min_length,
        under_min_length_points=scoring_data.under_min_length_points,
        top_x_catches=scoring_data.top_x_catches,
        display_order=scoring_data.display_order,
    )
    db.add(fish_scoring)
    await db.commit()
    await db.refresh(fish_scoring, ["fish"])

    return fish_scoring


@router.patch("/{event_id}/fish-scoring/{scoring_id}", response_model=EventFishScoringResponse)
async def update_fish_scoring(
    event_id: int,
    scoring_id: int,
    scoring_data: EventFishScoringUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> EventFishScoring:
    """Update a fish species scoring configuration."""
    # Get fish scoring
    query = (
        select(EventFishScoring)
        .options(selectinload(EventFishScoring.fish))
        .where(
            EventFishScoring.id == scoring_id,
            EventFishScoring.event_id == event_id,
        )
    )
    result = await db.execute(query)
    fish_scoring = result.scalar_one_or_none()
    if not fish_scoring:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Fish scoring configuration not found",
        )

    # Verify event access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this event",
        )

    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Can only modify fish scoring for draft events",
        )

    # Apply updates
    update_data = scoring_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(fish_scoring, field, value)

    await db.commit()
    await db.refresh(fish_scoring, ["fish"])

    return fish_scoring


@router.delete("/{event_id}/fish-scoring/{scoring_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_fish_scoring(
    event_id: int,
    scoring_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> None:
    """Remove a fish species from an event's scoring configuration."""
    # Get fish scoring
    query = select(EventFishScoring).where(
        EventFishScoring.id == scoring_id,
        EventFishScoring.event_id == event_id,
    )
    result = await db.execute(query)
    fish_scoring = result.scalar_one_or_none()
    if not fish_scoring:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Fish scoring configuration not found",
        )

    # Verify event access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this event",
        )

    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Can only modify fish scoring for draft events",
        )

    await db.delete(fish_scoring)
    await db.commit()


# ============== Event Species Bonus Points Endpoints ==============


@router.get("/{event_id}/bonus-points", response_model=list[EventSpeciesBonusPointsResponse])
async def list_event_bonus_points(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> list[EventSpeciesBonusPoints]:
    """Get all species bonus points configurations for an event."""
    # Verify event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    if not event_result.scalar_one_or_none():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Get bonus points configs
    query = (
        select(EventSpeciesBonusPoints)
        .where(EventSpeciesBonusPoints.event_id == event_id)
        .order_by(EventSpeciesBonusPoints.species_count)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/{event_id}/bonus-points", response_model=EventSpeciesBonusPointsResponse, status_code=http_status.HTTP_201_CREATED)
async def add_bonus_points(
    event_id: int,
    bonus_data: EventSpeciesBonusPointsCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> EventSpeciesBonusPoints:
    """Add a species bonus points configuration to an event."""
    # Verify event exists and user has access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check ownership
    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this event",
        )

    # Can only modify draft events
    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Can only modify bonus points for draft events",
        )

    # Check if species count already exists for this event
    existing_query = select(EventSpeciesBonusPoints).where(
        EventSpeciesBonusPoints.event_id == event_id,
        EventSpeciesBonusPoints.species_count == bonus_data.species_count,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Bonus points for {bonus_data.species_count} species already configured",
        )

    # Create bonus points config
    bonus_points = EventSpeciesBonusPoints(
        event_id=event_id,
        species_count=bonus_data.species_count,
        bonus_points=bonus_data.bonus_points,
    )
    db.add(bonus_points)
    await db.commit()
    await db.refresh(bonus_points)

    return bonus_points


@router.delete("/{event_id}/bonus-points/{bonus_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_bonus_points(
    event_id: int,
    bonus_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> None:
    """Remove a species bonus points configuration from an event."""
    # Get bonus points
    query = select(EventSpeciesBonusPoints).where(
        EventSpeciesBonusPoints.id == bonus_id,
        EventSpeciesBonusPoints.event_id == event_id,
    )
    result = await db.execute(query)
    bonus_points = result.scalar_one_or_none()
    if not bonus_points:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Bonus points configuration not found",
        )

    # Verify event access
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this event",
        )

    if event.status != EventStatus.DRAFT.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Can only modify bonus points for draft events",
        )

    await db.delete(bonus_points)
    await db.commit()


# ============== Event Start/Stop Endpoints ==============


async def broadcast_event_started(event_id: int, event_name: str) -> None:
    """Broadcast event started to live scoring subscribers via SSE."""
    from app.api.v1.live import live_scoring_service
    await live_scoring_service.broadcast(event_id, {
        "type": "event_started",
        "event_id": event_id,
        "event_name": event_name,
    })


async def broadcast_event_stopped(event_id: int, event_name: str) -> None:
    """Broadcast event stopped to live scoring subscribers via SSE."""
    from app.api.v1.live import live_scoring_service
    await live_scoring_service.broadcast(event_id, {
        "type": "event_stopped",
        "event_id": event_id,
        "event_name": event_name,
    })


@router.post("/{event_id}/start", response_model=EventListResponse, deprecated=True)
async def start_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="start" instead.

    Start a published event (set to ongoing).

    Constraints:
    - All enrolled users must be approved (no pending enrollments)
    - For team events, all approved users must be assigned to a team
    """
    from app.models.enrollment import EventEnrollment, EnrollmentStatus
    from app.models.team import TeamMember

    query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(Event.id == event_id)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to start this event",
        )

    if event.status != EventStatus.PUBLISHED.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Only published events can be started",
        )

    # Check approved enrollment count - must have at least 1 approved participant
    approved_query = select(func.count(EventEnrollment.id)).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.APPROVED.value,
    )
    approved_result = await db.execute(approved_query)
    approved_count = approved_result.scalar() or 0

    if approved_count == 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Cannot start event: No approved participants. At least one participant must be enrolled and approved.",
        )

    # Check for unapproved enrollments (pending status)
    pending_query = select(func.count(EventEnrollment.id)).where(
        EventEnrollment.event_id == event_id,
        EventEnrollment.status == EnrollmentStatus.PENDING.value,
    )
    pending_result = await db.execute(pending_query)
    pending_count = pending_result.scalar() or 0

    if pending_count > 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start event: {pending_count} enrolled user(s) pending approval. Approve or reject all pending enrollments first.",
        )

    # For team events, check that all approved users are assigned to a team
    if event.is_team_event:
        # Get count of approved enrollments
        approved_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        approved_result = await db.execute(approved_query)
        approved_count = approved_result.scalar() or 0

        # Get count of approved enrollments that have team assignments
        assigned_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event_id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
            EventEnrollment.id.in_(
                select(TeamMember.enrollment_id).where(TeamMember.is_active == True)
            ),
        )
        assigned_result = await db.execute(assigned_query)
        assigned_count = assigned_result.scalar() or 0

        unassigned_count = approved_count - assigned_count
        if unassigned_count > 0:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot start team event: {unassigned_count} approved user(s) not assigned to a team. Assign all participants to teams first.",
            )

    event.status = EventStatus.ONGOING.value
    await db.commit()

    # Broadcast event started via SSE
    background_tasks.add_task(broadcast_event_started, event.id, event.name)

    # Send FCM push notification to enrolled users
    from app.tasks.notifications import send_event_started_notifications
    send_event_started_notifications.delay(event.id, event.name)

    return event


@router.post("/{event_id}/stop", response_model=EventListResponse, deprecated=True)
async def stop_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> Event:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with action="stop" instead.

    Stop an ongoing event (set to completed).
    """
    query = (
        select(Event)
        .options(selectinload(Event.event_type))
        .where(Event.id == event_id)
    )
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.created_by_id != current_user.id and not current_user.profile.has_role("administrator"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to stop this event",
        )

    if event.status != EventStatus.ONGOING.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Only ongoing events can be stopped",
        )

    event.status = EventStatus.COMPLETED.value
    event.completed_at = datetime.now(timezone.utc)
    await db.commit()

    # Broadcast event stopped via SSE
    background_tasks.add_task(broadcast_event_stopped, event.id, event.name)

    # Send FCM push notification to enrolled users
    from app.tasks.notifications import send_event_stopped_notifications
    send_event_stopped_notifications.delay(event.id, event.name)

    # Trigger billing invoice generation
    from app.tasks.billing import generate_event_invoice
    generate_event_invoice.delay(event_id)

    # Trigger stats recalculation for all event participants
    from app.tasks.statistics import recalculate_event_stats
    recalculate_event_stats.delay(event_id)

    return event


@router.post("/{event_id}/force-status", response_model=EventStatusUpdateResponse, deprecated=True)
async def force_event_status(
    event_id: int,
    request: ForceStatusChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> dict:
    """
    [DEPRECATED] Use PATCH /events/{event_id}/status with force=true instead.

    Force change event status (admin/organizer override).

    Allows event owner or admin to force any status transition,
    bypassing normal validation rules. Used for recovering from
    accidental stops, restarts, or other operational mistakes.

    Requires a mandatory reason for audit logging.
    """
    query = select(Event).where(Event.id == event_id, Event.is_deleted == False)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    # Check authorization: event owner or admin
    is_admin = current_user.profile and current_user.profile.has_role("administrator")
    is_owner = event.created_by_id == current_user.id

    if not (is_admin or is_owner):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to force status change for this event",
        )

    # Don't allow same status
    if event.status == request.target_status.value:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Event is already in '{request.target_status.value}' status",
        )

    old_status = event.status

    # Update status
    event.status = request.target_status.value

    # Handle timestamp updates based on target status
    if request.target_status == EventStatus.ONGOING:
        # Restarting event: clear completed_at
        event.completed_at = None
    elif request.target_status == EventStatus.COMPLETED:
        # Completing event: set completed_at if not already set
        if not event.completed_at:
            event.completed_at = datetime.now(timezone.utc)
    elif request.target_status == EventStatus.DRAFT:
        # Reverting to draft: clear published_at
        event.published_at = None
    elif request.target_status == EventStatus.PUBLISHED:
        # Publishing: set published_at if not already set
        if not event.published_at:
            event.published_at = datetime.now(timezone.utc)
    # CANCELLED status doesn't need timestamp changes

    # Create audit log entry
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.EVENT_STATUS_FORCE_CHANGED.value,
        target_event_id=event_id,
        details={
            "from_status": old_status,
            "to_status": request.target_status.value,
            "reason": request.reason,
            "event_name": event.name,
        }
    )
    db.add(log)

    await db.commit()

    return {
        "id": event.id,
        "status": event.status,
        "is_deleted": event.is_deleted,
        "previous_status": old_status,
        "action_performed": f"force_{request.target_status.value}",
        "published_at": event.published_at,
        "completed_at": event.completed_at,
        "deleted_at": event.deleted_at,
    }


# ============== Event Validator Management Endpoints ==============


@router.get("/{event_id}/validators")
async def list_event_validators(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> list[dict]:
    """
    List all validators assigned to an event.
    Only event owner, assigned validators, or administrators can view.
    """
    # Verify event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_roles = current_user.profile.roles if current_user.profile else []

    # Check access
    is_admin = "administrator" in user_roles
    is_owner = event.created_by_id == current_user.id

    # Check if user is a validator for this event
    is_assigned_validator = False
    if "validator" in user_roles:
        validator_check = select(EventValidator).where(
            EventValidator.event_id == event_id,
            EventValidator.validator_id == current_user.id,
            EventValidator.is_active == True
        )
        validator_result = await db.execute(validator_check)
        is_assigned_validator = validator_result.scalar_one_or_none() is not None

    if not (is_admin or is_owner or is_assigned_validator):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view validators for this event"
        )

    # Get validators
    query = (
        select(EventValidator)
        .options(
            selectinload(EventValidator.validator).selectinload(UserAccount.profile),
            selectinload(EventValidator.assigned_by).selectinload(UserAccount.profile),
        )
        .where(
            EventValidator.event_id == event_id,
            EventValidator.is_active == True
        )
        .order_by(EventValidator.assigned_at)
    )
    result = await db.execute(query)
    validators = result.scalars().all()

    return [
        {
            "id": v.id,
            "validator_id": v.validator_id,
            "email": v.validator.email,
            "first_name": v.validator.profile.first_name if v.validator.profile else None,
            "last_name": v.validator.profile.last_name if v.validator.profile else None,
            "assigned_at": v.assigned_at.isoformat(),
            "assigned_by": {
                "id": v.assigned_by.id,
                "email": v.assigned_by.email,
                "first_name": v.assigned_by.profile.first_name if v.assigned_by and v.assigned_by.profile else None,
                "last_name": v.assigned_by.profile.last_name if v.assigned_by and v.assigned_by.profile else None,
            } if v.assigned_by else None
        }
        for v in validators
    ]


@router.post("/{event_id}/validators", status_code=http_status.HTTP_201_CREATED)
async def add_event_validator(
    event_id: int,
    validator_id: int = Query(..., description="User ID of the validator to assign"),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> dict:
    """
    Assign a validator to an event.
    Only the event owner or administrators can assign validators.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership (unless admin)
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can assign validators"
        )

    # Verify validator exists and has validator role
    validator_query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.id == validator_id)
    )
    validator_result = await db.execute(validator_query)
    validator = validator_result.scalar_one_or_none()

    if not validator:
        raise HTTPException(status_code=404, detail="Validator user not found")

    if not validator.profile or "validator" not in (validator.profile.roles or []):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="User does not have validator role"
        )

    # For non-admins, verify validator is from the organizer's club
    is_admin = "administrator" in user_roles
    if not is_admin:
        # Get organizer's club (event creator's club)
        organizer_club_query = select(Club).where(
            Club.owner_id == event.created_by_id,
            Club.is_deleted == False,
            Club.is_active == True
        )
        organizer_club_result = await db.execute(organizer_club_query)
        organizer_club = organizer_club_result.scalar_one_or_none()

        if not organizer_club:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Event organizer does not have an active club"
            )

        # Check if validator is an active member of the organizer's club
        membership_query = select(ClubMembership).where(
            ClubMembership.club_id == organizer_club.id,
            ClubMembership.user_id == validator_id,
            ClubMembership.status == MembershipStatus.ACTIVE.value
        )
        membership_result = await db.execute(membership_query)
        validator_membership = membership_result.scalar_one_or_none()

        if not validator_membership:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Validator must be a member of your club ({organizer_club.name})"
            )

    # Check if already assigned
    existing_query = select(EventValidator).where(
        EventValidator.event_id == event_id,
        EventValidator.validator_id == validator_id
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Validator is already assigned to this event"
            )
        else:
            # Reactivate existing assignment
            existing.is_active = True
            existing.assigned_by_id = current_user.id
            existing.assigned_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(existing)

            return {
                "id": existing.id,
                "validator_id": existing.validator_id,
                "email": validator.email,
                "first_name": validator.profile.first_name if validator.profile else None,
                "last_name": validator.profile.last_name if validator.profile else None,
                "assigned_at": existing.assigned_at.isoformat(),
                "message": "Validator reassigned to event"
            }

    # Create new assignment
    event_validator = EventValidator(
        event_id=event_id,
        validator_id=validator_id,
        assigned_by_id=current_user.id,
        is_active=True
    )
    db.add(event_validator)

    # Log the action
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.EVENT_VALIDATOR_ASSIGNED.value,
        target_user_id=validator_id,
        target_event_id=event_id,
        details={"validator_email": validator.email, "event_name": event.name}
    )
    db.add(log)

    await db.commit()
    await db.refresh(event_validator)

    return {
        "id": event_validator.id,
        "validator_id": event_validator.validator_id,
        "email": validator.email,
        "first_name": validator.profile.first_name if validator.profile else None,
        "last_name": validator.profile.last_name if validator.profile else None,
        "assigned_at": event_validator.assigned_at.isoformat(),
        "message": "Validator assigned to event"
    }


@router.delete("/{event_id}/validators/{validator_user_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def remove_event_validator(
    event_id: int,
    validator_user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(OrganizerOrAdmin),
) -> None:
    """
    Remove a validator from an event.
    Only the event owner or administrators can remove validators.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership (unless admin)
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can remove validators"
        )

    # Get validator assignment
    assignment_query = select(EventValidator).where(
        EventValidator.event_id == event_id,
        EventValidator.validator_id == validator_user_id,
        EventValidator.is_active == True
    )
    assignment_result = await db.execute(assignment_query)
    assignment = assignment_result.scalar_one_or_none()

    if not assignment:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Validator assignment not found"
        )

    # Soft delete - set is_active to False
    assignment.is_active = False

    # Log the action
    log = AdminActionLog(
        admin_id=current_user.id,
        action_type=AdminActionType.EVENT_VALIDATOR_REMOVED.value,
        target_user_id=validator_user_id,
        target_event_id=event_id,
        details={"event_name": event.name}
    )
    db.add(log)

    await db.commit()

    return {"message": "Validator removed successfully"}


# ============== Event Image Upload Endpoints ==============


@router.post("/{event_id}/image")
async def upload_event_image(
    event_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Upload an image for an event (logo/banner).
    Only event owner or admin can upload.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can upload images"
        )

    # Upload to storage
    image_url = await storage_service.upload_event_image(file, event_id)

    # Update event
    event.image_url = image_url
    await db.commit()

    return {"image_url": image_url}


@router.delete("/{event_id}/image")
async def delete_event_image(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Delete an event's image.
    Only event owner or admin can delete.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can delete images"
        )

    if not event.image_url:
        raise HTTPException(status_code=404, detail="Event has no image")

    # Delete from storage
    await storage_service.delete_file(event.image_url)

    # Update event
    event.image_url = None
    await db.commit()

    return {"message": "Image deleted successfully"}


# ============== Event Sponsors Endpoints ==============


@router.get("/{event_id}/sponsors")
async def list_event_sponsors(
    event_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    List sponsors associated with an event, grouped by tier.
    Public endpoint.
    """
    # Verify event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get event sponsors with sponsor details
    sponsors_query = (
        select(EventSponsor)
        .options(selectinload(EventSponsor.sponsor))
        .where(EventSponsor.event_id == event_id)
        .order_by(EventSponsor.display_order)
    )
    sponsors_result = await db.execute(sponsors_query)
    event_sponsors = sponsors_result.scalars().all()

    # Group by tier
    grouped = {tier.value: [] for tier in SponsorTier}
    for es in event_sponsors:
        sponsor = es.sponsor
        if sponsor and sponsor.is_active:
            tier_key = sponsor.tier if sponsor.tier in grouped else SponsorTier.PARTNER.value
            grouped[tier_key].append({
                "id": sponsor.id,
                "name": sponsor.name,
                "logo_url": sponsor.logo_url,
                "website_url": sponsor.website_url,
                "tier": sponsor.tier,
                "display_order": es.display_order,
            })

    return {
        "event_id": event_id,
        "tiers": [
            {"tier": tier.value, "name": tier.value.capitalize(), "sponsors": grouped[tier.value]}
            for tier in SponsorTier
        ],
        "total": sum(len(s) for s in grouped.values()),
    }


@router.post("/{event_id}/sponsors")
async def add_event_sponsors(
    event_id: int,
    sponsor_ids: list[int],
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Add sponsors to an event.
    Only event owner or admin can add sponsors.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can manage sponsors"
        )

    # Validate all sponsors exist and are active
    sponsors_query = select(Sponsor).where(
        Sponsor.id.in_(sponsor_ids),
        Sponsor.is_active == True
    )
    sponsors_result = await db.execute(sponsors_query)
    valid_sponsors = {s.id for s in sponsors_result.scalars().all()}

    invalid_ids = set(sponsor_ids) - valid_sponsors
    if invalid_ids:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or inactive sponsor IDs: {list(invalid_ids)}"
        )

    # Get existing event sponsors to avoid duplicates
    existing_query = select(EventSponsor.sponsor_id).where(EventSponsor.event_id == event_id)
    existing_result = await db.execute(existing_query)
    existing_ids = {row[0] for row in existing_result.all()}

    # Add new sponsors
    added = []
    for sponsor_id in sponsor_ids:
        if sponsor_id not in existing_ids:
            event_sponsor = EventSponsor(
                event_id=event_id,
                sponsor_id=sponsor_id,
                display_order=0,
            )
            db.add(event_sponsor)
            added.append(sponsor_id)

    await db.commit()

    return {
        "message": f"Added {len(added)} sponsors to event",
        "added_sponsor_ids": added,
        "already_existed": list(set(sponsor_ids) - set(added)),
    }


@router.delete("/{event_id}/sponsors/{sponsor_id}")
async def remove_event_sponsor(
    event_id: int,
    sponsor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Remove a sponsor from an event.
    Only event owner or admin can remove sponsors.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can manage sponsors"
        )

    # Find and delete the event sponsor
    es_query = select(EventSponsor).where(
        EventSponsor.event_id == event_id,
        EventSponsor.sponsor_id == sponsor_id
    )
    es_result = await db.execute(es_query)
    event_sponsor = es_result.scalar_one_or_none()

    if not event_sponsor:
        raise HTTPException(
            status_code=404,
            detail="Sponsor not associated with this event"
        )

    await db.delete(event_sponsor)
    await db.commit()

    return {"message": "Sponsor removed from event successfully"}


@router.put("/{event_id}/sponsors")
async def set_event_sponsors(
    event_id: int,
    sponsor_ids: list[int],
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Set the complete list of sponsors for an event (replaces existing).
    Only event owner or admin can set sponsors.
    """
    # Get event
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check ownership
    user_roles = current_user.profile.roles if current_user.profile else []
    if event.created_by_id != current_user.id and "administrator" not in user_roles:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can manage sponsors"
        )

    # Validate all sponsors exist and are active
    if sponsor_ids:
        sponsors_query = select(Sponsor).where(
            Sponsor.id.in_(sponsor_ids),
            Sponsor.is_active == True
        )
        sponsors_result = await db.execute(sponsors_query)
        valid_sponsors = {s.id for s in sponsors_result.scalars().all()}

        invalid_ids = set(sponsor_ids) - valid_sponsors
        if invalid_ids:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid or inactive sponsor IDs: {list(invalid_ids)}"
            )

    # Delete existing event sponsors
    delete_query = select(EventSponsor).where(EventSponsor.event_id == event_id)
    delete_result = await db.execute(delete_query)
    for es in delete_result.scalars().all():
        await db.delete(es)

    # Add new sponsors
    for sponsor_id in sponsor_ids:
        event_sponsor = EventSponsor(
            event_id=event_id,
            sponsor_id=sponsor_id,
            display_order=0,
        )
        db.add(event_sponsor)

    await db.commit()

    return {
        "message": f"Set {len(sponsor_ids)} sponsors for event",
        "sponsor_ids": sponsor_ids,
    }


# ============================================================================
# Event Prizes
# ============================================================================


@router.get("/{event_id}/prizes", response_model=PrizeListResponse)
async def list_event_prizes(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(get_current_user_optional),
):
    """
    Get all prizes for an event.
    Accessible by all users (prizes are public info).
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get prizes ordered by place
    query = (
        select(EventPrize)
        .where(EventPrize.event_id == event_id)
        .order_by(EventPrize.place)
    )
    result = await db.execute(query)
    prizes = result.scalars().all()

    return PrizeListResponse(
        items=[PrizeResponse.model_validate(p) for p in prizes],
        total=len(prizes),
    )


@router.post("/{event_id}/prizes", response_model=PrizeResponse, status_code=http_status.HTTP_201_CREATED)
async def create_event_prize(
    event_id: int,
    prize_data: PrizeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
):
    """
    Create a prize for an event.
    Only event organizers and admins can create prizes.
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check for duplicate place
    existing_query = select(EventPrize).where(
        EventPrize.event_id == event_id,
        EventPrize.place == prize_data.place,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Prize for place {prize_data.place} already exists",
        )

    # Create prize
    prize = EventPrize(
        event_id=event_id,
        place=prize_data.place,
        title=prize_data.title,
        description=prize_data.description,
        value=prize_data.value,
        image_url=prize_data.image_url,
    )
    db.add(prize)
    await db.commit()
    await db.refresh(prize)

    return PrizeResponse.model_validate(prize)


@router.put("/{event_id}/prizes/{prize_id}", response_model=PrizeResponse)
async def update_event_prize(
    event_id: int,
    prize_id: int,
    prize_data: PrizeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
):
    """
    Update an existing prize.
    Only event organizers and admins can update prizes.
    """
    # Get prize
    prize = await db.get(EventPrize, prize_id)
    if not prize or prize.event_id != event_id:
        raise HTTPException(status_code=404, detail="Prize not found")

    # Check for duplicate place if changing place
    if prize_data.place is not None and prize_data.place != prize.place:
        existing_query = select(EventPrize).where(
            EventPrize.event_id == event_id,
            EventPrize.place == prize_data.place,
            EventPrize.id != prize_id,
        )
        existing_result = await db.execute(existing_query)
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Prize for place {prize_data.place} already exists",
            )

    # Update fields
    update_data = prize_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(prize, field, value)

    await db.commit()
    await db.refresh(prize)

    return PrizeResponse.model_validate(prize)


@router.delete("/{event_id}/prizes/{prize_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_event_prize(
    event_id: int,
    prize_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
):
    """
    Delete a prize.
    Only event organizers and admins can delete prizes.
    """
    # Get prize
    prize = await db.get(EventPrize, prize_id)
    if not prize or prize.event_id != event_id:
        raise HTTPException(status_code=404, detail="Prize not found")

    await db.delete(prize)
    await db.commit()


@router.put("/{event_id}/prizes", response_model=PrizeListResponse)
async def bulk_update_event_prizes(
    event_id: int,
    bulk_data: PrizeBulkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(EventOwnerOrAdmin()),
):
    """
    Bulk update/replace all prizes for an event.
    This replaces all existing prizes with the provided list.
    Only event organizers and admins can use this.
    """
    # Verify event exists
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check for duplicate places in input
    places = [p.place for p in bulk_data.prizes]
    if len(places) != len(set(places)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate places in prize list",
        )

    # Delete all existing prizes
    delete_query = select(EventPrize).where(EventPrize.event_id == event_id)
    result = await db.execute(delete_query)
    existing_prizes = result.scalars().all()
    for prize in existing_prizes:
        await db.delete(prize)

    # Create new prizes
    new_prizes = []
    for prize_data in bulk_data.prizes:
        prize = EventPrize(
            event_id=event_id,
            place=prize_data.place,
            title=prize_data.title,
            description=prize_data.description,
            value=prize_data.value,
            image_url=prize_data.image_url,
        )
        db.add(prize)
        new_prizes.append(prize)

    await db.commit()

    # Refresh to get IDs
    for prize in new_prizes:
        await db.refresh(prize)

    return PrizeListResponse(
        items=[PrizeResponse.model_validate(p) for p in new_prizes],
        total=len(new_prizes),
    )
