"""Event lifecycle guard utilities.

These guards prevent modifications to events based on their status,
ensuring competition data integrity.
"""

from typing import List, Optional

from fastapi import HTTPException, status

from app.models.event import Event, EventStatus


class LifecycleError(HTTPException):
    """Custom exception for lifecycle validation errors."""

    def __init__(
        self,
        detail: str,
        current_status: str,
        code: str = "LIFECYCLE_ERROR",
    ):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": detail,
                "code": code,
                "status": current_status,
            },
        )


# Statuses that block lineup modifications
BLOCKED_STATUSES: List[EventStatus] = [
    EventStatus.ONGOING,
    EventStatus.COMPLETED,
    EventStatus.CANCELLED,
]

# Statuses that allow lineup modifications
MODIFIABLE_STATUSES: List[EventStatus] = [
    EventStatus.DRAFT,
    EventStatus.PUBLISHED,
]


def require_modifiable_status(
    event: Event,
    action: str = "modify lineups",
    blocked_statuses: Optional[List[EventStatus]] = None,
) -> None:
    """
    Raise LifecycleError if event status doesn't allow modifications.

    Args:
        event: The event to check
        action: Description of the action being attempted (for error message)
        blocked_statuses: Optional custom list of blocked statuses.
                         Defaults to ONGOING, COMPLETED, CANCELLED.

    Raises:
        LifecycleError: If event is in a blocked status

    Example:
        >>> require_modifiable_status(event, action="generate lineups")
        # Raises LifecycleError if event.status is ONGOING, COMPLETED, or CANCELLED
    """
    if blocked_statuses is None:
        blocked_statuses = BLOCKED_STATUSES

    if event.status in blocked_statuses:
        raise LifecycleError(
            detail=f"Cannot {action} for {event.status.value} event",
            current_status=event.status.value,
        )


def require_draft_status(event: Event, action: str = "perform this action") -> None:
    """
    Raise LifecycleError if event is not in DRAFT status.

    This is a stricter guard for operations that should only be allowed
    before the event is published.

    Args:
        event: The event to check
        action: Description of the action being attempted

    Raises:
        LifecycleError: If event is not in DRAFT status
    """
    # Handle both enum and string status values
    status_value = event.status.value if hasattr(event.status, "value") else event.status
    is_draft = status_value == EventStatus.DRAFT.value

    if not is_draft:
        raise LifecycleError(
            detail=f"Cannot {action} for {status_value} event. Event must be in draft status.",
            current_status=status_value,
        )


def require_not_completed(event: Event, action: str = "perform this action") -> None:
    """
    Raise LifecycleError if event is COMPLETED or CANCELLED.

    This is a more permissive guard that allows actions during ONGOING events.

    Args:
        event: The event to check
        action: Description of the action being attempted

    Raises:
        LifecycleError: If event is COMPLETED or CANCELLED
    """
    if event.status in [EventStatus.COMPLETED, EventStatus.CANCELLED]:
        raise LifecycleError(
            detail=f"Cannot {action} for {event.status.value} event",
            current_status=event.status.value,
        )
