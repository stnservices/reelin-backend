"""Admin-related models: AdminActionLog for audit logging."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount
    from app.models.event import Event


class AdminActionType(str, Enum):
    """Admin action types for audit logging."""

    USER_ACTIVATED = "user_activated"
    USER_DEACTIVATED = "user_deactivated"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_PROFILE_UPDATED = "user_profile_updated"
    USER_PASSWORD_RESET = "user_password_reset"
    EVENT_VALIDATOR_ASSIGNED = "event_validator_assigned"
    EVENT_VALIDATOR_REMOVED = "event_validator_removed"
    EVENT_STATUS_CHANGED = "event_status_changed"
    EVENT_STATUS_FORCE_CHANGED = "event_status_force_changed"


class AdminActionLog(Base):
    """
    Audit log for admin actions.

    Tracks all administrative actions for accountability and debugging.
    """

    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Who performed the action
    admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # What action was performed
    action_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )

    # Target user (if applicable)
    target_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Target event (if applicable)
    target_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Additional details stored as JSONB
    # Example: {"old_roles": ["angler"], "new_roles": ["angler", "organizer"]}
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # When the action occurred
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    admin: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[admin_id],
        lazy="joined"
    )
    target_user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount",
        foreign_keys=[target_user_id],
        lazy="joined"
    )
    target_event: Mapped[Optional["Event"]] = relationship(
        "Event",
        lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<AdminActionLog(id={self.id}, action={self.action_type}, admin={self.admin_id})>"
