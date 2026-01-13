"""Hall of Fame models: HallOfFameEntry for external achievements."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HallOfFameEntry(Base):
    """
    Hall of Fame entry for external achievements.
    Manually managed by admins for world championships, historical records,
    and achievements outside the ReelIn platform.
    """

    __tablename__ = "hall_of_fame_entries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Optional link to app user (for athletes who have accounts)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Athlete info (for external athletes without accounts)
    athlete_name: Mapped[str] = mapped_column(String(255), nullable=False)
    athlete_avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Achievement details
    achievement_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # 'world_champion', 'national_champion', 'world_podium', 'national_podium'
    competition_name: Mapped[str] = mapped_column(String(255), nullable=False)
    competition_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1, 2, 3, etc.

    # Format and category
    format_code: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, index=True
    )  # 'sf', 'ta', or NULL for general
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'individual', 'team', 'pairs'

    # Location
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Additional info
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Photo/certificate

    # Audit fields
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[user_id], lazy="joined"
    )
    created_by: Mapped[Optional["UserAccount"]] = relationship(
        "UserAccount", foreign_keys=[created_by_id], lazy="joined"
    )

    @property
    def display_name(self) -> str:
        """Return the name to display (user's name or athlete_name)."""
        if self.user:
            return f"{self.user.first_name} {self.user.last_name}"
        return self.athlete_name

    @property
    def avatar_url(self) -> Optional[str]:
        """Return the avatar URL (user's avatar or athlete_avatar_url)."""
        if self.user and self.user.avatar_url:
            return self.user.avatar_url
        return self.athlete_avatar_url

    def __repr__(self) -> str:
        return f"<HallOfFameEntry(id={self.id}, name={self.athlete_name}, type={self.achievement_type})>"


# Import for type hints
from app.models.user import UserAccount
