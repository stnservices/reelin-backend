"""Location models: Country, City, FishingSpot."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
    """Fishing spot/location model."""

    __tablename__ = "fishing_spots"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    city: Mapped["City"] = relationship("City", back_populates="fishing_spots", lazy="joined")

    def __repr__(self) -> str:
        return f"<FishingSpot(id={self.id}, name={self.name})>"
