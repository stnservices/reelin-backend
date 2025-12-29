"""Club and membership models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MembershipStatus(str, Enum):
    """Club membership status enum."""

    INVITED = "invited"
    ACTIVE = "active"
    DISMISSED = "dismissed"


class MembershipRole(str, Enum):
    """Club membership role enum."""

    MEMBER = "member"
    ADMIN = "admin"
    CAPTAIN = "captain"
    VALIDATOR = "validator"


class Club(Base):
    """Fishing club model."""

    __tablename__ = "clubs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    acronym: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Location
    country_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("countries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    city_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cities.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Owner
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    memberships: Mapped[list["ClubMembership"]] = relationship(
        "ClubMembership", back_populates="club", lazy="dynamic", cascade="all, delete-orphan"
    )
    country: Mapped[Optional["Country"]] = relationship("Country", lazy="joined")
    city: Mapped[Optional["City"]] = relationship("City", lazy="joined")

    def __repr__(self) -> str:
        return f"<Club(id={self.id}, name={self.name})>"


class ClubMembership(Base):
    """Club membership model with granular permissions."""

    __tablename__ = "club_memberships"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    club_id: Mapped[int] = mapped_column(
        ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Role and status
    role: Mapped[str] = mapped_column(
        String(20), default=MembershipRole.MEMBER.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=MembershipStatus.INVITED.value, nullable=False, index=True
    )

    # Granular permissions stored as JSONB
    # Example: {"can_create_events": true, "can_approve_members": false, "can_edit_club": false}
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # Invitation tracking
    invited_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Dismissal tracking
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    club: Mapped["Club"] = relationship("Club", back_populates="memberships")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    invited_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[invited_by_id], lazy="joined"
    )
    dismissed_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[dismissed_by_id], lazy="joined"
    )

    @property
    def is_invited(self) -> bool:
        return self.status == MembershipStatus.INVITED.value

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE.value

    @property
    def is_admin(self) -> bool:
        return self.role == MembershipRole.ADMIN.value

    def has_permission(self, permission: str) -> bool:
        """Check if member has a specific permission."""
        return self.permissions.get(permission, False)

    def __repr__(self) -> str:
        return f"<ClubMembership(id={self.id}, club_id={self.club_id}, user_id={self.user_id})>"


# Import for type hints
from app.models.user import UserAccount
from app.models.location import Country, City
