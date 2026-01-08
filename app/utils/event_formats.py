"""Event format utilities for achievement processing.

Provides helpers to:
- Map event types to format codes (sf, ta)
- Get participant IDs for different event formats
"""

import logging
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.sql.expression import distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import EventType

logger = logging.getLogger(__name__)


def get_format_code(event_type: Optional[EventType]) -> Optional[str]:
    """
    Map event type to format code for achievement filtering.

    Args:
        event_type: The EventType model instance

    Returns:
        Format code: "sf", "ta", or None if unknown
    """
    if event_type is None:
        return None

    # Map by event type code or name
    type_code = (event_type.code or "").lower()
    type_name = (event_type.name or "").lower()

    if "trout_area" in type_code or "trout area" in type_name:
        return "ta"
    else:
        return "sf"  # Default to street fishing


async def get_event_participant_ids(
    db: AsyncSession,
    event_id: int,
    format_code: str,
) -> List[int]:
    """
    Get user IDs of all participants for an event.

    Args:
        db: Database session
        event_id: Event ID
        format_code: Format code ("sf", "ta")

    Returns:
        List of participant user IDs (non-ghost, non-null)
    """
    if format_code == "ta":
        # TA: Get from TALineup
        from app.models.trout_area import TALineup
        result = await db.execute(
            select(distinct(TALineup.user_id))
            .where(TALineup.event_id == event_id)
            .where(TALineup.is_ghost == False)
            .where(TALineup.user_id.isnot(None))
        )
        return [row[0] for row in result.fetchall()]

    else:
        # SF: Get from EventParticipant
        from app.models.event_participant import EventParticipant
        result = await db.execute(
            select(distinct(EventParticipant.user_id))
            .where(EventParticipant.event_id == event_id)
            .where(EventParticipant.status == "approved")
        )
        return [row[0] for row in result.fetchall()]
