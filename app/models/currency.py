"""Currency model for participation fees."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Currency(Base):
    """Currency model for event participation fees.

    Stores currency information for displaying participation fees on events.
    Managed by administrators through the settings panel.
    """

    __tablename__ = "currencies"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # "Romanian Leu"
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)  # "RON"
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)  # "lei" or "RON"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Currency(id={self.id}, code={self.code}, name={self.name})>"
