"""Recommendation system models."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RecommendationDismissal(Base):
    """
    Tracks dismissed recommendations to avoid showing them again.
    """

    __tablename__ = "recommendation_dismissals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Type of recommendation: 'event' or 'angler'
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # ID of the dismissed item (event_id or user_id)
    item_id: Mapped[int] = mapped_column(nullable=False)

    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "item_type", "item_id", name="uq_dismissal_user_type_item"
        ),
        Index("idx_dismissals_user", "user_id"),
        Index("idx_dismissals_user_type", "user_id", "item_type"),
    )

    def __repr__(self) -> str:
        return f"<RecommendationDismissal(user_id={self.user_id}, item_type={self.item_type}, item_id={self.item_id})>"
