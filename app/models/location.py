"""Location models: Country, City, FishingSpot, MeetingPoint."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models import UserAccount


class Country(Base):
    """Country model."""

    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)  # ISO 3166-1 alpha-3
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    cities: Mapped[list["City"]] = relationship("City", back_populates="country", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Country(id={self.id}, name={self.name})>"


class City(Base):
    """City model."""

    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    country_id: Mapped[int] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    country: Mapped["Country"] = relationship("Country", back_populates="cities", lazy="joined")
    fishing_spots: Mapped[list["FishingSpot"]] = relationship(
        "FishingSpot", back_populates="city", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<City(id={self.id}, name={self.name})>"


class FishingSpot(Base):
    """Fishing spot/location model.

    owner_id: null = global/admin spot, non-null = organizer's private spot
    """

    __tablename__ = "fishing_spots"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    city: Mapped["City"] = relationship("City", back_populates="fishing_spots", lazy="joined")
    owner: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")
    meeting_points: Mapped[list["MeetingPoint"]] = relationship(
        "MeetingPoint", back_populates="fishing_spot", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<FishingSpot(id={self.id}, name={self.name})>"


class MeetingPoint(Base):
    """Meeting point within a fishing spot.

    owner_id: null = inherits from fishing spot, non-null = organizer's private meeting point
    """

    __tablename__ = "meeting_points"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    fishing_spot_id: Mapped[int] = mapped_column(
        ForeignKey("fishing_spots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    fishing_spot: Mapped["FishingSpot"] = relationship("FishingSpot", back_populates="meeting_points", lazy="joined")
    owner: Mapped[Optional["UserAccount"]] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<MeetingPoint(id={self.id}, name={self.name})>"
