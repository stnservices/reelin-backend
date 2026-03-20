"""Event status management service."""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    PreconditionFailedError,
    StatusTransitionError,
)
from app.models.admin import AdminActionLog, AdminActionType
from app.models.enrollment import EnrollmentStatus, EventEnrollment
from app.models.event import Event, EventStatus
from app.models.team import Team, TeamMember
from app.models.user import UserAccount
from app.models.trout_area import TALineup, TAGameCard, TAMatch, TAMatchStatus

logger = logging.getLogger(__name__)

# Define valid status transitions
VALID_TRANSITIONS = {
    EventStatus.DRAFT.value: [EventStatus.PUBLISHED.value],
    EventStatus.PUBLISHED.value: [
        EventStatus.DRAFT.value,
        EventStatus.ONGOING.value,
        EventStatus.CANCELLED.value,
    ],
    EventStatus.ONGOING.value: [EventStatus.COMPLETED.value],
    EventStatus.COMPLETED.value: [EventStatus.CANCELLED.value],
    EventStatus.CANCELLED.value: [],
}

# Map actions to target statuses
ACTION_TO_STATUS = {
    "publish": EventStatus.PUBLISHED.value,
    "recall": EventStatus.DRAFT.value,
    "start": EventStatus.ONGOING.value,
    "stop": EventStatus.COMPLETED.value,
    "cancel": EventStatus.CANCELLED.value,
}


class EventStatusService:
    """Service for managing event status transitions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_event(
        self,
        event_id: int,
        include_deleted: bool = False,
    ) -> Event:
        """Fetch event by ID with proper error handling."""
        query = select(Event).options(
            selectinload(Event.event_type),
            selectinload(Event.scoring_config),
        ).where(Event.id == event_id)

        if not include_deleted:
            query = query.where(Event.is_deleted == False)

        result = await self.db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            raise NotFoundError(
                message="Event not found",
                resource="Event",
                resource_id=event_id,
            )
        return event

    async def check_authorization(
        self,
        event: Event,
        user: UserAccount,
        action: str,
    ) -> None:
        """Check if user is authorized for the action."""
        is_admin = user.profile and user.profile.has_role("administrator")
        is_owner = event.created_by_id == user.id

        if is_admin or is_owner:
            return

        # Validators can only start/stop/cancel — not delete, restore, or force
        validator_allowed_actions = {"start", "stop", "cancel", "publish", "recall"}
        if action in validator_allowed_actions and user.profile and user.profile.has_role("validator"):
            from app.models.event_validator import EventValidator

            result = await self.db.execute(
                select(EventValidator).where(
                    EventValidator.event_id == event.id,
                    EventValidator.validator_id == user.id,
                    EventValidator.is_active == True,
                )
            )
            if result.scalar_one_or_none():
                return

        raise AuthorizationError(
            message=f"Not authorized to {action} this event",
            details={"action": action, "event_id": event.id},
        )

    def validate_transition(
        self,
        current_status: str,
        target_status: str,
        force: bool = False,
    ) -> None:
        """Validate status transition is allowed."""
        if force:
            return  # Force mode bypasses transition rules

        allowed = VALID_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise StatusTransitionError(
                message=f"Cannot transition from '{current_status}' to '{target_status}'",
                current_status=current_status,
                target_status=target_status,
                allowed_transitions=allowed,
            )

    async def check_start_preconditions(self, event: Event) -> list[str]:
        """
        Check preconditions for starting an event.

        Returns:
            List of warning messages (non-blocking issues).

        Raises:
            PreconditionFailedError: If blocking preconditions are not met.
        """
        warnings: list[str] = []

        # Must have at least one approved participant
        approved_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event.id,
            EventEnrollment.status == EnrollmentStatus.APPROVED.value,
        )
        approved_result = await self.db.execute(approved_query)
        approved_count = approved_result.scalar() or 0

        if approved_count == 0:
            raise PreconditionFailedError(
                message="Cannot start event: No approved participants",
                details={"approved_count": 0},
            )

        # No pending enrollments
        pending_query = select(func.count(EventEnrollment.id)).where(
            EventEnrollment.event_id == event.id,
            EventEnrollment.status == EnrollmentStatus.PENDING.value,
        )
        pending_result = await self.db.execute(pending_query)
        pending_count = pending_result.scalar() or 0

        if pending_count > 0:
            raise PreconditionFailedError(
                message=f"Cannot start event: {pending_count} enrollment(s) pending approval",
                details={"pending_count": pending_count},
            )

        # For team events, all approved users must be in active teams
        if event.is_team_event:
            # Subquery to get enrollment IDs that are in active teams with active membership
            active_team_members_subquery = (
                select(TeamMember.enrollment_id)
                .join(Team, TeamMember.team_id == Team.id)
                .where(
                    TeamMember.is_active == True,
                    Team.is_active == True,
                    Team.event_id == event.id,
                )
            )

            assigned_query = select(func.count(EventEnrollment.id)).where(
                EventEnrollment.event_id == event.id,
                EventEnrollment.status == EnrollmentStatus.APPROVED.value,
                EventEnrollment.id.in_(active_team_members_subquery),
            )
            assigned_result = await self.db.execute(assigned_query)
            assigned_count = assigned_result.scalar() or 0

            unassigned_count = approved_count - assigned_count
            if unassigned_count > 0:
                raise PreconditionFailedError(
                    message=f"Cannot start team event: {unassigned_count} participant(s) not assigned to active teams",
                    details={"unassigned_count": unassigned_count},
                )

        # For Trout Area events, check lineups and game cards exist
        if event.event_type and event.event_type.code == "trout_area":
            # Check for lineups
            lineup_query = select(func.count(TALineup.id)).where(
                TALineup.event_id == event.id,
                TALineup.is_ghost == False,
            )
            lineup_result = await self.db.execute(lineup_query)
            lineup_count = lineup_result.scalar() or 0

            if lineup_count == 0:
                raise PreconditionFailedError(
                    message="Cannot start Trout Area event: No lineups generated. Please generate lineups first.",
                    details={"lineup_count": 0},
                )

            # Check for game cards
            gamecard_query = select(func.count(TAGameCard.id)).where(
                TAGameCard.event_id == event.id,
            )
            gamecard_result = await self.db.execute(gamecard_query)
            gamecard_count = gamecard_result.scalar() or 0

            if gamecard_count == 0:
                raise PreconditionFailedError(
                    message="Cannot start Trout Area event: No game cards generated. Please generate lineups first.",
                    details={"gamecard_count": 0},
                )

            # Warning: Check for incomplete matches from previous run (non-blocking)
            incomplete_query = select(func.count(TAMatch.id)).where(
                TAMatch.event_id == event.id,
                TAMatch.status != TAMatchStatus.COMPLETED.value,
            )
            incomplete_result = await self.db.execute(incomplete_query)
            incomplete_count = incomplete_result.scalar() or 0

            if incomplete_count > 0:
                warnings.append(
                    f"Warning: {incomplete_count} incomplete match(es) from previous run"
                )

        return warnings

    async def update_status(
        self,
        event_id: int,
        action: str,
        user: UserAccount,
        reason: Optional[str] = None,
        force: bool = False,
    ) -> Tuple[Event, str, list[str]]:
        """
        Update event status based on action.

        Returns tuple of (updated_event, previous_status, warnings).
        """
        warnings: list[str] = []

        try:
            # Handle delete/restore separately
            if action == "delete":
                event, prev = await self._soft_delete(event_id, user)
                return event, prev, []
            elif action == "restore":
                event, prev = await self._restore(event_id, user)
                return event, prev, []

            # Get event
            event = await self.get_event(event_id)
            previous_status = event.status

            # Authorization check
            await self.check_authorization(event, user, "force" if force else action)

            # Get target status
            target_status = ACTION_TO_STATUS.get(action)
            if not target_status:
                raise StatusTransitionError(
                    message=f"Unknown action: {action}",
                    current_status=event.status,
                )

            # Same status check
            if event.status == target_status:
                raise StatusTransitionError(
                    message=f"Event is already in '{target_status}' status",
                    current_status=event.status,
                    target_status=target_status,
                )

            # Validate transition
            self.validate_transition(event.status, target_status, force)

            # Check preconditions for start
            if action == "start" and not force:
                warnings = await self.check_start_preconditions(event)

            # Update status
            event.status = target_status

            # Update timestamps
            now = datetime.now(timezone.utc)
            if target_status == EventStatus.PUBLISHED.value and not event.published_at:
                event.published_at = now
            elif target_status == EventStatus.COMPLETED.value and not event.completed_at:
                event.completed_at = now
            elif target_status == EventStatus.DRAFT.value:
                # Clear published_at when recalling to draft
                event.published_at = None

            # Audit log for force actions or cancel
            if (force or action == "cancel") and reason:
                action_type = (
                    AdminActionType.EVENT_STATUS_FORCE_CHANGED.value
                    if force
                    else AdminActionType.EVENT_CANCELLED.value
                    if hasattr(AdminActionType, "EVENT_CANCELLED")
                    else AdminActionType.EVENT_STATUS_FORCE_CHANGED.value
                )
                log = AdminActionLog(
                    admin_id=user.id,
                    action_type=action_type,
                    target_event_id=event_id,
                    details={
                        "from_status": previous_status,
                        "to_status": target_status,
                        "reason": reason,
                        "action": action,
                        "forced": force,
                    },
                )
                self.db.add(log)

            await self.db.commit()
            await self.db.refresh(event, ["event_type", "scoring_config"])

            return event, previous_status, warnings

        except (NotFoundError, AuthorizationError, StatusTransitionError, PreconditionFailedError):
            # Re-raise our custom exceptions
            raise
        except Exception as e:
            logger.error(f"Unexpected error in update_status: {e}", exc_info=True)
            raise

    async def _soft_delete(
        self,
        event_id: int,
        user: UserAccount,
    ) -> Tuple[Event, str]:
        """Soft delete an event."""
        try:
            event = await self.get_event(event_id)
            previous_status = event.status

            await self.check_authorization(event, user, "delete")

            # Cannot delete ongoing events
            if event.status == EventStatus.ONGOING.value:
                raise StatusTransitionError(
                    message="Cannot delete an ongoing event. Stop the event first.",
                    current_status=event.status,
                )

            event.is_deleted = True
            event.deleted_at = datetime.now(timezone.utc)
            event.deleted_by_id = user.id

            await self.db.commit()
            return event, previous_status

        except (NotFoundError, AuthorizationError, StatusTransitionError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in _soft_delete: {e}", exc_info=True)
            raise

    async def _restore(
        self,
        event_id: int,
        user: UserAccount,
    ) -> Tuple[Event, str]:
        """Restore a soft-deleted event."""
        try:
            event = await self.get_event(event_id, include_deleted=True)

            if not event.is_deleted:
                raise StatusTransitionError(
                    message="Event is not deleted",
                    current_status=event.status,
                )

            previous_status = event.status
            await self.check_authorization(event, user, "restore")

            event.is_deleted = False
            event.deleted_at = None
            event.deleted_by_id = None
            event.status = EventStatus.DRAFT.value  # Always restore to draft

            await self.db.commit()
            await self.db.refresh(event, ["event_type", "scoring_config"])

            return event, previous_status

        except (NotFoundError, AuthorizationError, StatusTransitionError):
            raise
        except Exception as e:
            logger.error(f"Unexpected error in _restore: {e}", exc_info=True)
            raise
