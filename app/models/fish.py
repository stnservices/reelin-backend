"""Fish species model."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Fish(Base):
    """Fish species model."""

    __tablename__ = "fish"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name_en: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # English translation
    name_ro: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Romanian translation
    scientific_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    min_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # cm
    max_length: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # cm
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Fish(id={self.id}, slug={self.slug}, name={self.name})>"
