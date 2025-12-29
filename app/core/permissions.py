"""Role-based permission decorators and utilities."""

from functools import wraps
from typing import TYPE_CHECKING, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_current_user, get_db
from app.models.user import UserAccount

if TYPE_CHECKING:
    from app.models.event import Event


def require_roles(*roles: str) -> Callable:
    """
    Decorator to require specific roles for an endpoint.
    User must have ALL specified roles.

    Usage:
        @router.post("/admin/users")
        @require_roles("administrator")
        async def admin_only_endpoint(current_user: UserAccount = Depends(get_current_user)):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get current_user from kwargs (injected by FastAPI)
            current_user: UserAccount = kwargs.get("current_user")
            if current_user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not current_user.profile:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User profile not found",
                )

            user_roles = set(current_user.profile.roles or [])
            required_roles = set(roles)

            # Check if user has ALL required roles
            if not required_roles.issubset(user_roles):
                missing_roles = required_roles - user_roles
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required roles: {', '.join(missing_roles)}",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_any_role(*roles: str) -> Callable:
    """
    Decorator to require ANY of the specified roles for an endpoint.
    User must have at least ONE of the specified roles.

    Usage:
        @router.get("/events/manage")
        @require_any_role("organizer", "administrator")
        async def organizer_or_admin_endpoint(current_user: UserAccount = Depends(get_current_user)):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_user: UserAccount = kwargs.get("current_user")
            if current_user is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )

            if not current_user.profile:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User profile not found",
                )

            user_roles = set(current_user.profile.roles or [])
            required_roles = set(roles)

            # Check if user has ANY of the required roles
            if not user_roles.intersection(required_roles):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires one of: {', '.join(required_roles)}",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


class RoleChecker:
    """
    Dependency class for role checking.
    Can be used as a FastAPI dependency.

    Usage:
        @router.get("/admin/dashboard")
        async def admin_dashboard(
            current_user: UserAccount = Depends(RoleChecker(["administrator"]))
        ):
            ...
    """

    def __init__(self, required_roles: list[str], require_all: bool = False):
        """
        Args:
            required_roles: List of role names
            require_all: If True, user must have ALL roles. If False, ANY role is sufficient.
        """
        self.required_roles = set(required_roles)
        self.require_all = require_all

    async def __call__(
        self, current_user: UserAccount = Depends(get_current_user)
    ) -> UserAccount:
        if not current_user.profile:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User profile not found",
            )

        user_roles = set(current_user.profile.roles or [])

        if self.require_all:
            # User must have ALL required roles
            if not self.required_roles.issubset(user_roles):
                missing = self.required_roles - user_roles
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required roles: {', '.join(missing)}",
                )
        else:
            # User must have ANY of the required roles
            if not user_roles.intersection(self.required_roles):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires one of: {', '.join(self.required_roles)}",
                )

        return current_user


# Pre-configured role checkers for common use cases
AdminOnly = RoleChecker(["administrator"])
OrganizerOrAdmin = RoleChecker(["organizer", "administrator"])
ValidatorOrAdmin = RoleChecker(["validator", "administrator"])
AnyStaffRole = RoleChecker(["organizer", "validator", "administrator"])


# =============================================================================
# Event-Based Permission Classes
# =============================================================================


class EventOwnerOrAdmin:
    """
    Dependency that verifies user is the event owner or an administrator.

    Use this for actions that only the event creator or admin should perform,
    such as editing event details or managing enrollments.

    Usage:
        @router.patch("/events/{event_id}")
        async def update_event(
            event_id: int,
            current_user: UserAccount = Depends(EventOwnerOrAdmin()),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """

    async def __call__(
        self,
        event_id: int,
        current_user: UserAccount = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> UserAccount:
        from app.models.event import Event

        # Get event
        query = select(Event).where(Event.id == event_id)
        result = await db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        # Check if admin
        if current_user.profile and "administrator" in (current_user.profile.roles or []):
            return current_user

        # Check if owner
        if event.created_by_id == current_user.id:
            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the event owner or administrator can perform this action"
        )


class EventOwnerOrValidatorOrAdmin:
    """
    Dependency that verifies user is event owner, assigned validator, or admin.

    Use this for actions that can be performed by any of:
    - The event creator/owner
    - A validator assigned to this specific event
    - An administrator

    Usage:
        @router.get("/events/{event_id}/catches")
        async def list_event_catches(
            event_id: int,
            current_user: UserAccount = Depends(EventOwnerOrValidatorOrAdmin()),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """

    async def __call__(
        self,
        event_id: int,
        current_user: UserAccount = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> UserAccount:
        from app.models.event import Event
        from app.models.event_validator import EventValidator

        # Get event
        query = select(Event).where(Event.id == event_id)
        result = await db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        user_roles = current_user.profile.roles if current_user.profile else []

        # Admin has full access
        if "administrator" in user_roles:
            return current_user

        # Owner has access to their events
        if event.created_by_id == current_user.id:
            return current_user

        # Check if user is an assigned validator for this event
        if "validator" in user_roles:
            validator_query = select(EventValidator).where(
                EventValidator.event_id == event_id,
                EventValidator.validator_id == current_user.id,
                EventValidator.is_active == True
            )
            validator_result = await db.execute(validator_query)
            if validator_result.scalar_one_or_none():
                return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Must be event owner, assigned validator, or administrator."
        )


class AssignedValidatorOrAdmin:
    """
    Dependency that verifies user is the assigned validator for this event or admin.

    Use this for validation-specific actions like approving/rejecting catches.

    Usage:
        @router.post("/catches/{catch_id}/approve")
        async def approve_catch(
            catch_id: int,
            event_id: int,
            current_user: UserAccount = Depends(AssignedValidatorOrAdmin()),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """

    async def __call__(
        self,
        event_id: int,
        current_user: UserAccount = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> UserAccount:
        from app.models.event import Event
        from app.models.event_validator import EventValidator

        # Get event
        query = select(Event).where(Event.id == event_id)
        result = await db.execute(query)
        event = result.scalar_one_or_none()

        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        user_roles = current_user.profile.roles if current_user.profile else []

        # Admin has full access
        if "administrator" in user_roles:
            return current_user

        # Check if user is an assigned validator for this event
        if "validator" in user_roles:
            validator_query = select(EventValidator).where(
                EventValidator.event_id == event_id,
                EventValidator.validator_id == current_user.id,
                EventValidator.is_active == True
            )
            validator_result = await db.execute(validator_query)
            if validator_result.scalar_one_or_none():
                return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned validator or administrator can perform this action"
        )


async def check_is_event_validator(
    event_id: int,
    user_id: int,
    db: AsyncSession
) -> bool:
    """
    Helper function to check if a user is an assigned validator for an event.

    Args:
        event_id: The event ID to check
        user_id: The user ID to check
        db: Database session

    Returns:
        True if user is an active validator for the event, False otherwise
    """
    from app.models.event_validator import EventValidator

    query = select(EventValidator).where(
        EventValidator.event_id == event_id,
        EventValidator.validator_id == user_id,
        EventValidator.is_active == True
    )
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


async def get_event_with_permission_check(
    event_id: int,
    current_user: UserAccount,
    db: AsyncSession,
    require_owner: bool = False,
    require_validator: bool = False,
) -> "Event":
    """
    Helper function to get an event with permission checking.

    Args:
        event_id: The event ID to retrieve
        current_user: The current user making the request
        db: Database session
        require_owner: If True, only allow owner or admin
        require_validator: If True, also allow assigned validators

    Returns:
        The Event object if user has permission

    Raises:
        HTTPException: 404 if event not found, 403 if no permission
    """
    from app.models.event import Event
    from app.models.event_validator import EventValidator

    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_roles = current_user.profile.roles if current_user.profile else []

    # Admin always has access
    if "administrator" in user_roles:
        return event

    # Owner has access
    if event.created_by_id == current_user.id:
        return event

    # Check validator access if allowed
    if require_validator and "validator" in user_roles:
        is_validator = await check_is_event_validator(event_id, current_user.id, db)
        if is_validator:
            return event

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have permission to access this event"
    )
