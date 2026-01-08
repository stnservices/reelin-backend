"""Admin settings management endpoints for base configuration data."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.permissions import AdminOnly
from app.models.user import UserAccount
from app.models.event import EventType, ScoringConfig
from app.models.fish import Fish
from app.models.sponsor import Sponsor
from app.models.location import Country, City, FishingSpot
from app.models.event import Event, EventFishScoring
from app.models.event_sponsor import EventSponsor
from app.models.currency import Currency
from app.models.settings import VideoDurationOption

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class EventTypeCreate(BaseModel):
    """Schema for creating an event type."""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z_]+$')
    description: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: bool = True


class EventTypeUpdate(BaseModel):
    """Schema for updating an event type."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: Optional[bool] = None


class EventTypeResponse(BaseModel):
    """Schema for event type response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: bool


class ScoringConfigCreate(BaseModel):
    """Schema for creating a scoring config."""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z_]+$')
    description: Optional[str] = None
    default_top_x: int = Field(default=10, ge=1)
    default_catch_slots: int = Field(default=5, ge=1)
    rules: dict = Field(default_factory=dict)
    event_type_ids: list[int] = Field(default_factory=list)  # Assign to multiple event types
    is_active: bool = True


class ScoringConfigUpdate(BaseModel):
    """Schema for updating a scoring config."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    default_top_x: Optional[int] = Field(None, ge=1)
    default_catch_slots: Optional[int] = Field(None, ge=1)
    rules: Optional[dict] = None
    event_type_ids: Optional[list[int]] = None  # Update event type assignments
    is_active: Optional[bool] = None


class ScoringConfigResponse(BaseModel):
    """Schema for scoring config response."""
    id: int
    name: str
    code: str
    description: Optional[str] = None
    default_top_x: int
    default_catch_slots: int
    rules: dict
    is_active: bool
    event_types: list[dict] = []  # List of {id, code, name}


class FishCreate(BaseModel):
    """Schema for creating a fish species."""
    name: str = Field(..., min_length=1, max_length=100)
    name_en: Optional[str] = Field(None, max_length=100)
    name_ro: Optional[str] = Field(None, max_length=100)
    scientific_name: Optional[str] = Field(None, max_length=150)
    min_length: Optional[float] = Field(None, ge=0)
    max_length: Optional[float] = Field(None, ge=0)
    image_url: Optional[str] = None
    is_active: bool = True


class FishUpdate(BaseModel):
    """Schema for updating a fish species."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    name_en: Optional[str] = Field(None, max_length=100)
    name_ro: Optional[str] = Field(None, max_length=100)
    scientific_name: Optional[str] = Field(None, max_length=150)
    min_length: Optional[float] = Field(None, ge=0)
    max_length: Optional[float] = Field(None, ge=0)
    image_url: Optional[str] = None
    is_active: Optional[bool] = None


class FishResponse(BaseModel):
    """Schema for fish species response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    name_en: Optional[str] = None
    name_ro: Optional[str] = None
    scientific_name: Optional[str] = None
    min_length: Optional[float] = None
    max_length: Optional[float] = None
    image_url: Optional[str] = None
    is_active: bool


class SponsorCreate(BaseModel):
    """Schema for creating a sponsor."""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    contact_email: Optional[str] = None
    display_order: int = 0
    is_active: bool = True


class SponsorUpdate(BaseModel):
    """Schema for updating a sponsor."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    contact_email: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None
    is_global: Optional[bool] = None  # If True, sets owner_id to None (global sponsor)


class SponsorResponse(BaseModel):
    """Schema for sponsor response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
    contact_email: Optional[str] = None
    tier: str
    display_order: int
    is_active: bool
    owner_id: Optional[int] = None
    owner_email: Optional[str] = None
    is_global: bool = True


class CountryCreate(BaseModel):
    """Schema for creating a country."""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=2, max_length=3)


class CountryResponse(BaseModel):
    """Schema for country response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str


class CityCreate(BaseModel):
    """Schema for creating a city."""
    country_id: int
    name: str = Field(..., min_length=1, max_length=100)


class CityResponse(BaseModel):
    """Schema for city response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    country_id: int
    name: str


class FishingSpotCreate(BaseModel):
    """Schema for creating a fishing spot."""
    city_id: int
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class FishingSpotUpdate(BaseModel):
    """Schema for updating a fishing spot."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class FishingSpotResponse(BaseModel):
    """Schema for fishing spot response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    city_id: int
    name: str
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CurrencyCreate(BaseModel):
    """Schema for creating a currency."""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=3, max_length=3)
    symbol: str = Field(..., min_length=1, max_length=10)


class CurrencyUpdate(BaseModel):
    """Schema for updating a currency."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    symbol: Optional[str] = Field(None, min_length=1, max_length=10)
    is_active: Optional[bool] = None


class CurrencyResponse(BaseModel):
    """Schema for currency response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    symbol: str
    is_active: bool


class VideoDurationCreate(BaseModel):
    """Schema for creating a video duration option."""
    seconds: int = Field(..., ge=1, le=60)
    label: str = Field(..., min_length=1, max_length=50)
    display_order: int = Field(default=0)


class VideoDurationUpdate(BaseModel):
    """Schema for updating a video duration option."""
    seconds: Optional[int] = Field(None, ge=1, le=60)
    label: Optional[str] = Field(None, min_length=1, max_length=50)
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class VideoDurationResponse(BaseModel):
    """Schema for video duration option response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    seconds: int
    label: str
    is_active: bool
    display_order: int


# ============================================================================
# EVENT TYPES
# ============================================================================


@router.get("/event-types", response_model=list[EventTypeResponse])
async def list_event_types(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[EventType]:
    """List all event types (admin can see inactive ones too)."""
    query = select(EventType)
    if not include_inactive:
        query = query.where(EventType.is_active == True)
    query = query.order_by(EventType.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/event-types", response_model=EventTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_event_type(
    data: EventTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> EventType:
    """Create a new event type."""
    # Check uniqueness
    existing = await db.execute(
        select(EventType).where(
            (EventType.name == data.name) | (EventType.code == data.code)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Event type with this name or code already exists",
        )

    event_type = EventType(**data.model_dump())
    db.add(event_type)
    await db.commit()
    await db.refresh(event_type)
    return event_type


@router.patch("/event-types/{event_type_id}", response_model=EventTypeResponse)
async def update_event_type(
    event_type_id: int,
    data: EventTypeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> EventType:
    """Update an event type."""
    result = await db.execute(select(EventType).where(EventType.id == event_type_id))
    event_type = result.scalar_one_or_none()

    if not event_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(event_type, field, value)

    await db.commit()
    await db.refresh(event_type)
    return event_type


@router.delete("/event-types/{event_type_id}")
async def delete_event_type(
    event_type_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete an event type.
    - If not used in any events: hard delete (remove from DB)
    - If used in events: returns warning, use force=true to soft-delete (deactivate)
    """
    result = await db.execute(select(EventType).where(EventType.id == event_type_id))
    event_type = result.scalar_one_or_none()

    if not event_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")

    # Check if event type is used in any events (including deleted ones for safety)
    usage_query = select(Event).where(Event.event_type_id == event_type_id)
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events:
        if not force:
            event_count = len(used_in_events)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"This event type is used in {event_count} event(s). Use force=true to deactivate anyway.",
                    "usage_count": event_count,
                    "can_force_delete": True,
                }
            )
        # Soft delete by setting is_active to False (when force=true)
        event_type.is_active = False
        await db.commit()
        return {
            "message": f"Event type '{event_type.name}' has been deactivated",
            "affected_events": len(used_in_events),
        }
    else:
        # Hard delete - not used anywhere
        name = event_type.name
        await db.delete(event_type)
        await db.commit()
        return {
            "message": f"Event type '{name}' has been deleted",
            "affected_events": 0,
        }


# ============================================================================
# SCORING CONFIGS
# ============================================================================


def _scoring_config_to_response(config: ScoringConfig) -> dict:
    """Convert ScoringConfig to response dict with event_types."""
    return {
        "id": config.id,
        "name": config.name,
        "code": config.code,
        "description": config.description,
        "default_top_x": config.default_top_x,
        "default_catch_slots": config.default_catch_slots,
        "rules": config.rules,
        "is_active": config.is_active,
        "event_types": [
            {"id": et.id, "code": et.code, "name": et.name}
            for et in config.event_types
        ],
    }


@router.get("/scoring-configs")
async def list_scoring_configs(
    event_type_id: Optional[int] = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all scoring configurations sorted alphabetically by name."""
    from sqlalchemy.orm import selectinload
    from app.models.event import scoring_config_event_types

    query = select(ScoringConfig).options(selectinload(ScoringConfig.event_types))

    if event_type_id:
        # Filter by event type using the M2M relationship
        query = query.join(scoring_config_event_types).where(
            scoring_config_event_types.c.event_type_id == event_type_id
        )

    if not include_inactive:
        query = query.where(ScoringConfig.is_active == True)

    # Sort alphabetically by name
    query = query.order_by(ScoringConfig.name.asc())

    result = await db.execute(query)
    configs = result.scalars().unique().all()

    return [_scoring_config_to_response(c) for c in configs]


@router.post("/scoring-configs", status_code=status.HTTP_201_CREATED)
async def create_scoring_config(
    data: ScoringConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Create a new scoring configuration with optional event type assignments."""
    from sqlalchemy.orm import selectinload

    # Check code uniqueness
    existing = await db.execute(select(ScoringConfig).where(ScoringConfig.code == data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scoring config with this code already exists",
        )

    # Get event types if provided
    event_types = []
    if data.event_type_ids:
        result = await db.execute(
            select(EventType).where(EventType.id.in_(data.event_type_ids))
        )
        event_types = result.scalars().all()
        if len(event_types) != len(data.event_type_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more invalid event type IDs",
            )

    # Create config without event_type_ids
    config_data = data.model_dump(exclude={"event_type_ids"})
    scoring_config = ScoringConfig(**config_data)
    scoring_config.event_types = event_types

    db.add(scoring_config)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(ScoringConfig)
        .options(selectinload(ScoringConfig.event_types))
        .where(ScoringConfig.id == scoring_config.id)
    )
    scoring_config = result.scalar_one()

    return _scoring_config_to_response(scoring_config)


@router.patch("/scoring-configs/{config_id}")
async def update_scoring_config(
    config_id: int,
    data: ScoringConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Update a scoring configuration including event type assignments."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(ScoringConfig)
        .options(selectinload(ScoringConfig.event_types))
        .where(ScoringConfig.id == config_id)
    )
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoring config not found")

    update_data = data.model_dump(exclude_unset=True)

    # Handle event_type_ids separately
    if "event_type_ids" in update_data:
        event_type_ids = update_data.pop("event_type_ids")
        if event_type_ids is not None:
            result = await db.execute(
                select(EventType).where(EventType.id.in_(event_type_ids))
            )
            config.event_types = result.scalars().all()

    # Update other fields
    for field, value in update_data.items():
        setattr(config, field, value)

    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(ScoringConfig)
        .options(selectinload(ScoringConfig.event_types))
        .where(ScoringConfig.id == config_id)
    )
    config = result.scalar_one()

    return _scoring_config_to_response(config)




# ============================================================================
# FISH SPECIES
# ============================================================================


@router.get("/fish", response_model=list[FishResponse])
async def list_fish(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[Fish]:
    """List all fish species."""
    query = select(Fish)
    if not include_inactive:
        query = query.where(Fish.is_active == True)
    query = query.order_by(Fish.name)
    result = await db.execute(query)
    return result.scalars().all()


def generate_fish_slug(name: str) -> str:
    """Generate a URL-friendly slug from a fish name."""
    import re
    import unicodedata
    # Normalize unicode characters
    slug = unicodedata.normalize('NFKD', name.lower())
    # Remove non-ASCII characters
    slug = slug.encode('ascii', 'ignore').decode('ascii')
    # Replace spaces and special chars with hyphens
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[-\s]+', '-', slug).strip('-')
    return slug


@router.post("/fish", response_model=FishResponse, status_code=status.HTTP_201_CREATED)
async def create_fish(
    data: FishCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Fish:
    """Create a new fish species."""
    existing = await db.execute(select(Fish).where(Fish.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Fish species with this name already exists",
        )

    # Generate slug from name
    slug = generate_fish_slug(data.name)

    # Check if slug already exists and make unique if needed
    existing_slug = await db.execute(select(Fish).where(Fish.slug == slug))
    if existing_slug.scalar_one_or_none():
        # Append a number to make it unique
        counter = 2
        while True:
            new_slug = f"{slug}-{counter}"
            existing_check = await db.execute(select(Fish).where(Fish.slug == new_slug))
            if not existing_check.scalar_one_or_none():
                slug = new_slug
                break
            counter += 1

    fish = Fish(**data.model_dump(), slug=slug)
    db.add(fish)
    await db.commit()
    await db.refresh(fish)
    return fish


@router.patch("/fish/{fish_id}", response_model=FishResponse)
async def update_fish(
    fish_id: int,
    data: FishUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Fish:
    """Update a fish species."""
    result = await db.execute(select(Fish).where(Fish.id == fish_id))
    fish = result.scalar_one_or_none()

    if not fish:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fish not found")

    update_data = data.model_dump(exclude_unset=True)

    # If name is being updated, regenerate the slug
    if "name" in update_data and update_data["name"] != fish.name:
        new_slug = generate_fish_slug(update_data["name"])
        # Check if slug already exists (excluding current fish)
        existing_slug = await db.execute(
            select(Fish).where(Fish.slug == new_slug, Fish.id != fish_id)
        )
        if existing_slug.scalar_one_or_none():
            counter = 2
            while True:
                test_slug = f"{new_slug}-{counter}"
                existing_check = await db.execute(
                    select(Fish).where(Fish.slug == test_slug, Fish.id != fish_id)
                )
                if not existing_check.scalar_one_or_none():
                    new_slug = test_slug
                    break
                counter += 1
        update_data["slug"] = new_slug

    for field, value in update_data.items():
        setattr(fish, field, value)

    await db.commit()
    await db.refresh(fish)
    return fish


@router.delete("/fish/{fish_id}")
async def delete_fish(
    fish_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a fish species.
    Returns warning if fish is used in events. Use force=true to soft-delete anyway.
    """
    result = await db.execute(select(Fish).where(Fish.id == fish_id))
    fish = result.scalar_one_or_none()

    if not fish:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fish species not found")

    # Check if fish is used in any events
    usage_query = (
        select(EventFishScoring)
        .join(Event, EventFishScoring.event_id == Event.id)
        .where(
            EventFishScoring.fish_id == fish_id,
            Event.status.in_(["draft", "published", "ongoing"])
        )
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This fish species is used in {event_count} active event(s). Use force=true to deactivate anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Soft delete by setting is_active to False
    fish.is_active = False
    await db.commit()

    return {
        "message": f"Fish species '{fish.name}' has been deactivated",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


# ============================================================================
# SPONSORS
# ============================================================================


@router.get("/sponsors")
async def list_sponsors(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """List all sponsors ordered by display_order.

    Admin can see all sponsors (global + organizer-owned) with owner info.
    """
    query = select(Sponsor)
    if not include_inactive:
        query = query.where(Sponsor.is_active == True)
    query = query.order_by(Sponsor.display_order, Sponsor.name)
    result = await db.execute(query)
    sponsors = result.scalars().all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "logo_url": s.logo_url,
            "website_url": s.website_url,
            "contact_email": s.contact_email,
            "display_order": s.display_order,
            "is_active": s.is_active,
            "owner_id": s.owner_id,
            "owner_email": s.owner.email if s.owner else None,
            "is_global": s.owner_id is None,
        }
        for s in sponsors
    ]


@router.post("/sponsors", status_code=status.HTTP_201_CREATED)
async def create_sponsor(
    data: SponsorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Create a new global sponsor (owner_id = NULL).

    Global sponsors are available to all organizers for their events.
    """
    # Check for duplicate name among global sponsors
    existing = await db.execute(
        select(Sponsor).where(
            Sponsor.name == data.name,
            Sponsor.owner_id.is_(None)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A global sponsor with this name already exists",
        )

    # Create global sponsor (owner_id = None)
    sponsor = Sponsor(**data.model_dump())
    db.add(sponsor)
    await db.commit()
    await db.refresh(sponsor)

    return {
        "id": sponsor.id,
        "name": sponsor.name,
        "description": sponsor.description,
        "logo_url": sponsor.logo_url,
        "website_url": sponsor.website_url,
        "contact_email": sponsor.contact_email,
        "display_order": sponsor.display_order,
        "is_active": sponsor.is_active,
        "owner_id": None,
        "owner_email": None,
        "is_global": True,
    }


@router.patch("/sponsors/{sponsor_id}")
async def update_sponsor(
    sponsor_id: int,
    data: SponsorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
):
    """Update a sponsor. Admin can update any sponsor (global or organizer-owned)."""
    result = await db.execute(select(Sponsor).where(Sponsor.id == sponsor_id))
    sponsor = result.scalar_one_or_none()

    if not sponsor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sponsor not found")

    update_data = data.model_dump(exclude_unset=True)

    # Handle is_global flag - convert to owner_id
    if "is_global" in update_data:
        is_global = update_data.pop("is_global")
        if is_global:
            sponsor.owner_id = None  # Make it global
        # Note: to make it non-global, admin would need to assign to a specific user

    for field, value in update_data.items():
        setattr(sponsor, field, value)

    await db.commit()
    await db.refresh(sponsor)

    return {
        "id": sponsor.id,
        "name": sponsor.name,
        "description": sponsor.description,
        "logo_url": sponsor.logo_url,
        "website_url": sponsor.website_url,
        "contact_email": sponsor.contact_email,
        "display_order": sponsor.display_order,
        "is_active": sponsor.is_active,
        "owner_id": sponsor.owner_id,
        "owner_email": sponsor.owner.email if sponsor.owner else None,
        "is_global": sponsor.owner_id is None,
    }


@router.delete("/sponsors/{sponsor_id}")
async def delete_sponsor(
    sponsor_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a sponsor.
    Returns warning if sponsor is used in events. Use force=true to soft-delete anyway.
    """
    result = await db.execute(select(Sponsor).where(Sponsor.id == sponsor_id))
    sponsor = result.scalar_one_or_none()

    if not sponsor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sponsor not found")

    # Check if sponsor is used in any events
    usage_query = (
        select(EventSponsor)
        .join(Event, EventSponsor.event_id == Event.id)
        .where(
            EventSponsor.sponsor_id == sponsor_id,
            Event.status.in_(["draft", "published", "ongoing"])
        )
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This sponsor is associated with {event_count} active event(s). Use force=true to deactivate anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Soft delete by setting is_active to False
    sponsor.is_active = False
    await db.commit()

    return {
        "message": f"Sponsor '{sponsor.name}' has been deactivated",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


# ============================================================================
# CURRENCIES
# ============================================================================


@router.get("/currencies", response_model=list[CurrencyResponse])
async def list_currencies(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[Currency]:
    """List all currencies."""
    query = select(Currency)
    if not include_inactive:
        query = query.where(Currency.is_active == True)
    query = query.order_by(Currency.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/currencies", response_model=CurrencyResponse, status_code=status.HTTP_201_CREATED)
async def create_currency(
    data: CurrencyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Currency:
    """Create a new currency."""
    # Normalize code to uppercase
    code = data.code.upper()

    # Check uniqueness
    existing = await db.execute(select(Currency).where(Currency.code == code))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Currency with this code already exists",
        )

    currency = Currency(
        name=data.name,
        code=code,
        symbol=data.symbol,
    )
    db.add(currency)
    await db.commit()
    await db.refresh(currency)
    return currency


@router.patch("/currencies/{currency_id}", response_model=CurrencyResponse)
async def update_currency(
    currency_id: int,
    data: CurrencyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Currency:
    """Update a currency. Code is immutable after creation."""
    result = await db.execute(select(Currency).where(Currency.id == currency_id))
    currency = result.scalar_one_or_none()

    if not currency:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Currency not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(currency, field, value)

    await db.commit()
    await db.refresh(currency)
    return currency


@router.delete("/currencies/{currency_id}")
async def delete_currency(
    currency_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a currency.
    Returns warning if currency is used in events. Use force=true to soft-delete anyway.
    """
    result = await db.execute(select(Currency).where(Currency.id == currency_id))
    currency = result.scalar_one_or_none()

    if not currency:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Currency not found")

    # Check if currency is used in any events
    usage_query = select(Event).where(
        Event.participation_fee_currency_id == currency_id,
        Event.status.in_(["draft", "published", "ongoing"])
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This currency is used in {event_count} active event(s). Use force=true to deactivate anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Soft delete by setting is_active to False
    currency.is_active = False
    await db.commit()

    return {
        "message": f"Currency '{currency.code}' has been deactivated",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


# ============================================================================
# LOCATIONS (Countries, Cities, Fishing Spots)
# ============================================================================


@router.get("/countries", response_model=list[CountryResponse])
async def list_countries(
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[Country]:
    """List all countries."""
    query = select(Country).order_by(Country.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/countries", response_model=CountryResponse, status_code=status.HTTP_201_CREATED)
async def create_country(
    data: CountryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Country:
    """Create a new country."""
    existing = await db.execute(
        select(Country).where((Country.name == data.name) | (Country.code == data.code))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Country with this name or code already exists",
        )

    country = Country(**data.model_dump())
    db.add(country)
    await db.commit()
    await db.refresh(country)
    return country


@router.patch("/countries/{country_id}", response_model=CountryResponse)
async def update_country(
    country_id: int,
    data: CountryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> Country:
    """Update a country."""
    result = await db.execute(select(Country).where(Country.id == country_id))
    country = result.scalar_one_or_none()
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found",
        )

    # Check for duplicate name/code (excluding current country)
    existing = await db.execute(
        select(Country).where(
            ((Country.name == data.name) | (Country.code == data.code))
            & (Country.id != country_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Country with this name or code already exists",
        )

    for key, value in data.model_dump().items():
        setattr(country, key, value)

    await db.commit()
    await db.refresh(country)
    return country


@router.get("/cities", response_model=list[CityResponse])
async def list_cities(
    country_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[City]:
    """List all cities, optionally filtered by country."""
    query = select(City)
    if country_id:
        query = query.where(City.country_id == country_id)
    query = query.order_by(City.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/cities", response_model=CityResponse, status_code=status.HTTP_201_CREATED)
async def create_city(
    data: CityCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> City:
    """Create a new city."""
    # Verify country exists
    country_result = await db.execute(select(Country).where(Country.id == data.country_id))
    if not country_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid country",
        )

    city = City(**data.model_dump())
    db.add(city)
    await db.commit()
    await db.refresh(city)
    return city


@router.patch("/cities/{city_id}", response_model=CityResponse)
async def update_city(
    city_id: int,
    data: CityCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> City:
    """Update a city."""
    result = await db.execute(select(City).where(City.id == city_id))
    city = result.scalar_one_or_none()
    if not city:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="City not found",
        )

    # Verify country exists if country_id is being changed
    if data.country_id != city.country_id:
        country_result = await db.execute(select(Country).where(Country.id == data.country_id))
        if not country_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid country",
            )

    for key, value in data.model_dump().items():
        setattr(city, key, value)

    await db.commit()
    await db.refresh(city)
    return city


@router.get("/fishing-spots", response_model=list[FishingSpotResponse])
async def list_fishing_spots(
    city_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[FishingSpot]:
    """List all fishing spots, optionally filtered by city."""
    query = select(FishingSpot)
    if city_id:
        query = query.where(FishingSpot.city_id == city_id)
    query = query.order_by(FishingSpot.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/fishing-spots", response_model=FishingSpotResponse, status_code=status.HTTP_201_CREATED)
async def create_fishing_spot(
    data: FishingSpotCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> FishingSpot:
    """Create a new fishing spot."""
    # Verify city exists
    city_result = await db.execute(select(City).where(City.id == data.city_id))
    if not city_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid city",
        )

    spot = FishingSpot(**data.model_dump())
    db.add(spot)
    await db.commit()
    await db.refresh(spot)
    return spot


@router.patch("/fishing-spots/{spot_id}", response_model=FishingSpotResponse)
async def update_fishing_spot(
    spot_id: int,
    data: FishingSpotUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> FishingSpot:
    """Update a fishing spot."""
    result = await db.execute(select(FishingSpot).where(FishingSpot.id == spot_id))
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fishing spot not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(spot, field, value)

    await db.commit()
    await db.refresh(spot)
    return spot


@router.delete("/fishing-spots/{spot_id}")
async def delete_fishing_spot(
    spot_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a fishing spot.
    Returns warning if location is used in events. Use force=true to delete anyway.
    """
    result = await db.execute(select(FishingSpot).where(FishingSpot.id == spot_id))
    spot = result.scalar_one_or_none()

    if not spot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fishing spot not found")

    # Check if fishing spot is used in any events
    usage_query = select(Event).where(
        Event.location_id == spot_id,
        Event.status.in_(["draft", "published", "ongoing"])
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This fishing spot is used in {event_count} active event(s). Use force=true to delete anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # For fishing spots, we do a hard delete (they're location data)
    # Events using this location will have location_id set to NULL via ondelete="SET NULL"
    await db.delete(spot)
    await db.commit()

    return {
        "message": f"Fishing spot '{spot.name}' has been deleted",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


@router.delete("/cities/{city_id}")
async def delete_city(
    city_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a city. Will cascade delete all fishing spots in the city.
    Returns warning if any fishing spots are used in events. Use force=true to delete anyway.
    """
    result = await db.execute(select(City).where(City.id == city_id))
    city = result.scalar_one_or_none()

    if not city:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="City not found")

    # Check if any fishing spots in this city are used in events
    usage_query = (
        select(Event)
        .join(FishingSpot, Event.location_id == FishingSpot.id)
        .where(
            FishingSpot.city_id == city_id,
            Event.status.in_(["draft", "published", "ongoing"])
        )
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Fishing spots in this city are used in {event_count} active event(s). Use force=true to delete anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Hard delete - will cascade delete fishing spots
    await db.delete(city)
    await db.commit()

    return {
        "message": f"City '{city.name}' and all its fishing spots have been deleted",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


@router.delete("/countries/{country_id}")
async def delete_country(
    country_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a country. Will cascade delete all cities and fishing spots.
    Returns warning if any fishing spots are used in events. Use force=true to delete anyway.
    """
    result = await db.execute(select(Country).where(Country.id == country_id))
    country = result.scalar_one_or_none()

    if not country:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Country not found")

    # Check if any fishing spots in this country are used in events
    usage_query = (
        select(Event)
        .join(FishingSpot, Event.location_id == FishingSpot.id)
        .join(City, FishingSpot.city_id == City.id)
        .where(
            City.country_id == country_id,
            Event.status.in_(["draft", "published", "ongoing"])
        )
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Locations in this country are used in {event_count} active event(s). Use force=true to delete anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Hard delete - will cascade delete cities and fishing spots
    await db.delete(country)
    await db.commit()

    return {
        "message": f"Country '{country.name}' and all its locations have been deleted",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }


# ============================================================================
# VIDEO DURATION OPTIONS
# ============================================================================


@router.get("/video-durations", response_model=list[VideoDurationResponse])
async def list_video_durations(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> list[VideoDurationOption]:
    """List all video duration options."""
    query = select(VideoDurationOption)
    if not include_inactive:
        query = query.where(VideoDurationOption.is_active == True)
    query = query.order_by(VideoDurationOption.display_order, VideoDurationOption.seconds)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/video-durations", response_model=VideoDurationResponse, status_code=status.HTTP_201_CREATED)
async def create_video_duration(
    data: VideoDurationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> VideoDurationOption:
    """Create a new video duration option."""
    # Check uniqueness
    existing = await db.execute(
        select(VideoDurationOption).where(VideoDurationOption.seconds == data.seconds)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Video duration option for {data.seconds} seconds already exists",
        )

    duration = VideoDurationOption(
        seconds=data.seconds,
        label=data.label,
        display_order=data.display_order,
    )
    db.add(duration)
    await db.commit()
    await db.refresh(duration)
    return duration


@router.patch("/video-durations/{duration_id}", response_model=VideoDurationResponse)
async def update_video_duration(
    duration_id: int,
    data: VideoDurationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> VideoDurationOption:
    """Update a video duration option."""
    result = await db.execute(
        select(VideoDurationOption).where(VideoDurationOption.id == duration_id)
    )
    duration = result.scalar_one_or_none()

    if not duration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video duration option not found")

    update_data = data.model_dump(exclude_unset=True)

    # Check uniqueness if seconds is being updated
    if "seconds" in update_data and update_data["seconds"] != duration.seconds:
        existing = await db.execute(
            select(VideoDurationOption).where(
                VideoDurationOption.seconds == update_data["seconds"],
                VideoDurationOption.id != duration_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Video duration option for {update_data['seconds']} seconds already exists",
            )

    for field, value in update_data.items():
        setattr(duration, field, value)

    await db.commit()
    await db.refresh(duration)
    return duration


@router.delete("/video-durations/{duration_id}")
async def delete_video_duration(
    duration_id: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: UserAccount = Depends(AdminOnly),
) -> dict:
    """
    Delete a video duration option.
    Returns warning if duration is used in events. Use force=true to soft-delete anyway.
    """
    result = await db.execute(
        select(VideoDurationOption).where(VideoDurationOption.id == duration_id)
    )
    duration = result.scalar_one_or_none()

    if not duration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video duration option not found")

    # Check if duration is used in any events
    usage_query = select(Event).where(
        Event.max_video_duration == duration.seconds,
        Event.status.in_(["draft", "published", "ongoing"])
    )
    usage_result = await db.execute(usage_query)
    used_in_events = usage_result.scalars().all()

    if used_in_events and not force:
        event_count = len(used_in_events)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"This video duration is used in {event_count} active event(s). Use force=true to deactivate anyway.",
                "usage_count": event_count,
                "can_force_delete": True,
            }
        )

    # Soft delete by setting is_active to False
    duration.is_active = False
    await db.commit()

    return {
        "message": f"Video duration '{duration.label}' has been deactivated",
        "affected_events": len(used_in_events) if used_in_events else 0,
    }
