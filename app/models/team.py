"""Team-related models for team events."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TeamMemberRole(str, Enum):
    """Team member role types."""
    CAPTAIN = "captain"
    MEMBER = "member"


class Team(Base):
    """
    Team model for team-based events.
    A team belongs to a single event and has multiple members.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    team_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Auto-assigned team number

    # Team captain/creator
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Optional team details
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Unique constraint: team name must be unique within an event
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="uq_team_event_name"),
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="teams")
    created_by: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    members: Mapped[list["TeamMember"]] = relationship(
        "TeamMember", back_populates="team", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def member_count(self) -> int:
        """Get the number of active members in the team."""
        return sum(1 for m in self.members if m.is_active)

    def __repr__(self) -> str:
        return f"<Team(id={self.id}, name={self.name}, event_id={self.event_id})>"


class TeamMember(Base):
    """
    Team member model linking users to teams via their enrollment.
    """

    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("event_enrollments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Role within the team
    role: Mapped[str] = mapped_column(
        String(20), default=TeamMemberRole.MEMBER.value, nullable=False
    )

    # Who added this member
    added_by_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Unique constraint: one enrollment can only belong to one team per event
    __table_args__ = (
        UniqueConstraint("team_id", "enrollment_id", name="uq_team_member_enrollment"),
    )

    # Relationships
    team: Mapped["Team"] = relationship("Team", back_populates="members")
    enrollment: Mapped["EventEnrollment"] = relationship("EventEnrollment", back_populates="team_membership")
    added_by: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<TeamMember(id={self.id}, team_id={self.team_id}, enrollment_id={self.enrollment_id})>"


# Import for type hints (avoid circular imports)
from app.models.event import Event
from app.models.user import UserAccount
from app.models.enrollment import EventEnrollment
