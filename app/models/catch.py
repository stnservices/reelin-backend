"""Catch and scoring models: Catch, EventScoreboard, RankingMovement."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CatchStatus(str, Enum):
    """Catch validation status enum."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Catch(Base):
    """
    Individual fish catch record (formerly Leaderboard in old system).
    Represents a single catch submitted by a participant.
    """

    __tablename__ = "catches"
    __table_args__ = (
        UniqueConstraint('event_id', 'user_id', 'sha256_original', name='uq_catches_event_user_sha256'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fish_id: Mapped[int] = mapped_column(
        ForeignKey("fish.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Catch details
    length: Mapped[float] = mapped_column(Float, nullable=False)  # in cm
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # in kg, optional

    # Photo/Video evidence
    photo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    poster_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Video poster frame

    # Media fingerprint (for deduplication)
    sha256_original: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    original_mime_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    original_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    video_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Location (from photo EXIF or manual entry)
    location_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # GPS accuracy in meters

    # Scoring
    points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Calculated after validation

    # Validation
    status: Mapped[str] = mapped_column(
        String(20), default=CatchStatus.PENDING.value, nullable=False, index=True
    )
    validated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Revalidation (for correcting validation mistakes)
    revalidated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    revalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revalidation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Proxy upload tracking (when organizer/validator uploads on behalf of angler)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    catch_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Actual catch time from EXIF or user input

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="catches")
    user: Mapped["UserAccount"] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    fish: Mapped["Fish"] = relationship("Fish", lazy="joined")
    validated_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[validated_by_id], lazy="joined"
    )
    revalidated_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[revalidated_by_id], lazy="joined"
    )
    uploaded_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[uploaded_by_id], lazy="joined"
    )
    ai_analysis: Mapped[Optional["CatchAiAnalysis"]] = relationship(
        "CatchAiAnalysis", back_populates="catch", uselist=False, lazy="joined"
    )

    @property
    def is_pending(self) -> bool:
        return self.status == CatchStatus.PENDING.value

    @property
    def is_approved(self) -> bool:
        return self.status == CatchStatus.APPROVED.value

    @property
    def is_rejected(self) -> bool:
        return self.status == CatchStatus.REJECTED.value

    @property
    def is_proxy_upload(self) -> bool:
        """Check if this catch was uploaded by someone other than the angler."""
        return self.uploaded_by_id is not None and self.uploaded_by_id != self.user_id

    def __repr__(self) -> str:
        return f"<Catch(id={self.id}, event_id={self.event_id}, status={self.status})>"


class EventScoreboard(Base):
    """
    Aggregated scoreboard for each participant in an event.
    Updated after each catch validation.

    Includes all ranking criteria and tiebreakers for export:
    - team_id: The team the user belonged to during this event (for team events)
    - club_id: The club the user was an active member of at event time
    - species_count: Number of distinct species caught
    - average_length: Average length of all catches
    - first_catch_time: Timestamp of first approved catch (tiebreaker)

    6-level tiebreaker order: total_points > total_catches > species_count >
    best_catch_length > average_length > first_catch_time (earlier wins)
    """

    __tablename__ = "event_scoreboards"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Team tracking (for team events)
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Club tracking (user's active club at time of event participation)
    club_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Aggregated scores
    total_catches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # Sum of all catch lengths
    total_weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Sum of all catch weights
    total_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Bonus and penalty points
    bonus_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Species diversity bonus
    penalty_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Deducted for violations

    # Tiebreaker fields (for complete export-ready scoreboard)
    species_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # Distinct species caught
    average_length: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # Avg catch length
    first_catch_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Earliest approved catch (tiebreaker: earlier wins)

    # Best catch stats
    best_catch_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_catch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catches.id", ondelete="SET NULL"), nullable=True
    )

    # Ranking
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    previous_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="scoreboards")
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    best_catch: Mapped[Optional["Catch"]] = relationship("Catch", lazy="joined")
    team: Mapped[Optional["Team"]] = relationship("Team", lazy="joined")
    club: Mapped[Optional["Club"]] = relationship("Club", lazy="joined")

    def __repr__(self) -> str:
        return f"<EventScoreboard(id={self.id}, event_id={self.event_id}, rank={self.rank})>"


class RankingMovement(Base):
    """
    Tracks ranking changes for live scoring updates.
    Used for SSE broadcasts and history.

    For individual events: uses user_id
    For team events: uses team_id
    """

    __tablename__ = "ranking_movements"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # For individual events
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # For team events
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )

    old_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    new_rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # The catch that triggered this movement
    catch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catches.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event")
    user: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")
    team: Mapped[Optional["Team"]] = relationship("Team", lazy="joined")
    catch: Mapped[Optional["Catch"]] = relationship("Catch", lazy="joined")

    @property
    def movement(self) -> int:
        """Positive = moved up, Negative = moved down."""
        return self.old_rank - self.new_rank

    @property
    def movement_emoji(self) -> str:
        """Return emoji for movement direction."""
        if self.movement > 0:
            return "🔼"
        elif self.movement < 0:
            return "🔽"
        return "⏹"

    def __repr__(self) -> str:
        return f"<RankingMovement(id={self.id}, {self.old_rank} -> {self.new_rank})>"


# Import for type hints
from app.models.event import Event
from app.models.user import UserAccount
from app.models.fish import Fish
from app.models.team import Team
from app.models.club import Club
from app.models.ai_analysis import CatchAiAnalysis
