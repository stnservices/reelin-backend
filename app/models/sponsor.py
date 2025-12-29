"""Sponsor model."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount


class SponsorTier(str, Enum):
    """Sponsor tier levels - determines visibility and placement."""

    PLATINUM = "platinum"  # Top billing, largest logo display
    GOLD = "gold"          # Premium placement
    SILVER = "silver"      # Standard prominent placement
    BRONZE = "bronze"      # Standard placement
    PARTNER = "partner"    # Supporting partner


# Tier display order for sorting (lower = higher priority)
TIER_ORDER = {
    SponsorTier.PLATINUM: 1,
    SponsorTier.GOLD: 2,
    SponsorTier.SILVER: 3,
    SponsorTier.BRONZE: 4,
    SponsorTier.PARTNER: 5,
}


class Sponsor(Base):
    """Sponsor model.

    Sponsors can be:
    - Global (owner_id = NULL): Managed by admins, available to all events
    - Organizer-owned (owner_id = user_id): Managed by specific organizer
    """

    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tier: Mapped[str] = mapped_column(
        String(20), default=SponsorTier.PARTNER.value, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Owner: NULL = global sponsor, user_id = organizer-owned
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # Relationships
    owner: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")

    @property
    def is_global(self) -> bool:
        """Check if this is a global sponsor (no owner)."""
        return self.owner_id is None

    def __repr__(self) -> str:
        return f"<Sponsor(id={self.id}, name={self.name}, owner_id={self.owner_id})>"
