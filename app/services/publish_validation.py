"""Publish readiness validation service for events.

This service validates that an event has all required fields configured
before it can be published. Validation rules vary by event type.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.models.event import Event, EventStatus, EventFishScoring
from app.models.trout_area import TAEventSettings

logger = logging.getLogger(__name__)


# =============================================================================
# i18n Message Keys
# =============================================================================

class ValidationKeys:
    """i18n message keys for publish validation errors."""

    # Common validation keys
    MISSING_NAME = "validation.publish.missing_name"
    MISSING_LOCATION = "validation.publish.missing_location"
    MISSING_START_DATE = "validation.publish.missing_start_date"
    MISSING_END_DATE = "validation.publish.missing_end_date"
    START_DATE_IN_PAST = "validation.publish.start_date_in_past"
    END_DATE_BEFORE_START = "validation.publish.end_date_before_start"

    # SF-specific keys
    SF_NO_SPECIES = "validation.publish.sf.no_species"
    SF_NO_SCORING_CONFIG = "validation.publish.sf.no_scoring_config"

    # TA-specific keys
    TA_NO_SETTINGS = "validation.publish.ta.no_settings"
    TA_NO_ALGORITHM = "validation.publish.ta.no_algorithm"
    TA_NO_LEGS = "validation.publish.ta.no_legs"


class PublishValidationService:
    """Service for validating event publish readiness."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_event(self, event_id: int) -> Event:
        """
        Fetch event with all related data needed for validation.

        Args:
            event_id: The event ID to fetch

        Returns:
            Event model with relationships loaded

        Raises:
            NotFoundError: If event doesn't exist
        """
        query = (
            select(Event)
            .options(
                selectinload(Event.event_type),
                selectinload(Event.scoring_config),
                selectinload(Event.ta_settings),
            )
            .where(Event.id == event_id, Event.is_deleted == False)
        )

        result = await self.db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            raise NotFoundError(
                message="Event not found",
                resource="Event",
                resource_id=event_id,
            )
        return event

    async def validate_publish_readiness(
        self,
        event_id: int,
    ) -> Tuple[bool, List[str], Dict[str, bool]]:
        """
        Validate if an event is ready to be published.

        Args:
            event_id: The event ID to validate

        Returns:
            Tuple of (is_ready, missing_items, checks)
            - is_ready: True if all validations pass
            - missing_items: List of i18n message keys for failed validations
            - checks: Dict mapping check names to their pass/fail status
        """
        event = await self.get_event(event_id)

        missing_items: List[str] = []
        checks: Dict[str, bool] = {}

        # Run common validations
        common_missing, common_checks = self._validate_common(event)
        missing_items.extend(common_missing)
        checks.update(common_checks)

        # Run event-type-specific validations
        format_code = event.event_type.code if event.event_type else None

        if format_code == "sf":
            sf_missing, sf_checks = await self._validate_sf(event)
            missing_items.extend(sf_missing)
            checks.update(sf_checks)
        elif format_code == "ta":
            ta_missing, ta_checks = self._validate_ta(event)
            missing_items.extend(ta_missing)
            checks.update(ta_checks)

        is_ready = len(missing_items) == 0

        return is_ready, missing_items, checks

    def _validate_common(self, event: Event) -> Tuple[List[str], Dict[str, bool]]:
        """
        Validate common fields required for all event types.

        Args:
            event: The event to validate

        Returns:
            Tuple of (missing_items, checks)
        """
        missing_items: List[str] = []
        checks: Dict[str, bool] = {}

        # Check name
        has_name = bool(event.name and event.name.strip())
        checks["has_name"] = has_name
        if not has_name:
            missing_items.append(ValidationKeys.MISSING_NAME)

        # Check location (either a FishingSpot relationship or location_name string)
        has_location = bool(
            event.location is not None
            or (event.location_name and event.location_name.strip())
        )
        checks["has_location"] = has_location
        if not has_location:
            missing_items.append(ValidationKeys.MISSING_LOCATION)

        # Check start date exists
        has_start_date = event.start_date is not None
        checks["has_start_date"] = has_start_date
        if not has_start_date:
            missing_items.append(ValidationKeys.MISSING_START_DATE)

        # Check end date exists
        has_end_date = event.end_date is not None
        checks["has_end_date"] = has_end_date
        if not has_end_date:
            missing_items.append(ValidationKeys.MISSING_END_DATE)

        # Check start date is in future (only if start_date exists)
        if has_start_date:
            now = datetime.now(timezone.utc)
            # Make start_date timezone-aware if it isn't already
            start_date = event.start_date
            if start_date.tzinfo is None:
                start_date = start_date.replace(tzinfo=timezone.utc)

            start_in_future = start_date > now
            checks["start_date_in_future"] = start_in_future
            if not start_in_future:
                missing_items.append(ValidationKeys.START_DATE_IN_PAST)

        # Check end date is after start date (only if both exist)
        if has_start_date and has_end_date:
            end_after_start = event.end_date > event.start_date
            checks["end_date_after_start"] = end_after_start
            if not end_after_start:
                missing_items.append(ValidationKeys.END_DATE_BEFORE_START)

        return missing_items, checks

    async def _validate_sf(self, event: Event) -> Tuple[List[str], Dict[str, bool]]:
        """
        Validate Street Fishing (SF) specific fields.

        Args:
            event: The event to validate

        Returns:
            Tuple of (missing_items, checks)
        """
        missing_items: List[str] = []
        checks: Dict[str, bool] = {}

        # Check for at least one allowed species (via EventFishScoring)
        species_query = select(func.count(EventFishScoring.id)).where(
            EventFishScoring.event_id == event.id
        )
        result = await self.db.execute(species_query)
        species_count = result.scalar() or 0

        has_species = species_count > 0
        checks["sf_has_species"] = has_species
        if not has_species:
            missing_items.append(ValidationKeys.SF_NO_SPECIES)

        # Check scoring config exists
        has_scoring_config = event.scoring_config is not None
        checks["sf_has_scoring_config"] = has_scoring_config
        if not has_scoring_config:
            missing_items.append(ValidationKeys.SF_NO_SCORING_CONFIG)

        return missing_items, checks

    def _validate_ta(self, event: Event) -> Tuple[List[str], Dict[str, bool]]:
        """
        Validate Trout Area (TA) specific fields.

        Args:
            event: The event to validate

        Returns:
            Tuple of (missing_items, checks)
        """
        missing_items: List[str] = []
        checks: Dict[str, bool] = {}

        settings: TAEventSettings | None = event.ta_settings

        # Check settings exist
        has_settings = settings is not None
        checks["ta_has_settings"] = has_settings

        if not has_settings:
            missing_items.append(ValidationKeys.TA_NO_SETTINGS)
            # Cannot check other TA fields without settings
            checks["ta_has_legs"] = False
            return missing_items, checks

        # Check number of legs is set and valid
        has_legs = settings.number_of_legs is not None and settings.number_of_legs > 0
        checks["ta_has_legs"] = has_legs
        if not has_legs:
            missing_items.append(ValidationKeys.TA_NO_LEGS)

        return missing_items, checks
