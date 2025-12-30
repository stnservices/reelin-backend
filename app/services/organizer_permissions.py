"""Organizer permission checking service."""

import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organizer_permissions import OrganizerEventTypeAccess, NationalEventOrganizer
from app.models.event import EventType

logger = logging.getLogger(__name__)


class OrganizerPermissionService:
    """Service for checking organizer permissions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_event_type_access(self, user_id: int, event_type_id: int) -> bool:
        """
        Check if user can create events of this type.

        Args:
            user_id: The user ID to check
            event_type_id: The event type ID to check access for

        Returns:
            True if user has active access, False otherwise
        """
        result = await self.db.execute(
            select(OrganizerEventTypeAccess)
            .where(
                OrganizerEventTypeAccess.user_id == user_id,
                OrganizerEventTypeAccess.event_type_id == event_type_id,
                OrganizerEventTypeAccess.is_active == True,
            )
        )
        access = result.scalar_one_or_none()

        if access:
            logger.debug(f"User {user_id} has access to event type {event_type_id}")
        else:
            logger.debug(f"User {user_id} does NOT have access to event type {event_type_id}")

        return access is not None

    async def check_national_permission(self, user_id: int) -> bool:
        """
        Check if user can create national events.

        Args:
            user_id: The user ID to check

        Returns:
            True if user has active national permission, False otherwise
        """
        result = await self.db.execute(
            select(NationalEventOrganizer)
            .where(
                NationalEventOrganizer.user_id == user_id,
                NationalEventOrganizer.is_active == True,
            )
        )
        permission = result.scalar_one_or_none()

        if permission:
            logger.debug(f"User {user_id} has national event permission")
        else:
            logger.debug(f"User {user_id} does NOT have national event permission")

        return permission is not None

    async def get_accessible_event_types(self, user_id: int) -> List[EventType]:
        """
        Get all event types this user can create.

        Args:
            user_id: The user ID to get accessible types for

        Returns:
            List of EventType objects the user can create events for
        """
        result = await self.db.execute(
            select(EventType)
            .join(
                OrganizerEventTypeAccess,
                EventType.id == OrganizerEventTypeAccess.event_type_id
            )
            .where(
                OrganizerEventTypeAccess.user_id == user_id,
                OrganizerEventTypeAccess.is_active == True,
                EventType.is_active == True,
            )
            .order_by(EventType.name)
        )
        event_types = list(result.scalars().all())

        logger.debug(f"User {user_id} has access to {len(event_types)} event types")
        return event_types

    async def get_accessible_event_type_ids(self, user_id: int) -> List[int]:
        """
        Get IDs of all event types this user can create.

        Args:
            user_id: The user ID to get accessible type IDs for

        Returns:
            List of event type IDs
        """
        result = await self.db.execute(
            select(OrganizerEventTypeAccess.event_type_id)
            .where(
                OrganizerEventTypeAccess.user_id == user_id,
                OrganizerEventTypeAccess.is_active == True,
            )
        )
        return list(result.scalars().all())

    async def get_user_permissions_summary(self, user_id: int) -> dict:
        """
        Get a summary of all permissions for a user.

        Args:
            user_id: The user ID to get summary for

        Returns:
            Dict with event_types list and can_create_national flag
        """
        event_types = await self.get_accessible_event_types(user_id)
        can_create_national = await self.check_national_permission(user_id)

        return {
            "event_types": event_types,
            "can_create_national": can_create_national,
        }
