"""Minigame models: MinigameScore for fishing minigame high scores."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount


class MinigameScore(Base):
    """
    Minigame score model.

    Stores high scores from the virtual fishing minigame.
    """

    __tablename__ = "minigame_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fish_caught: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="JSON array of fish caught during session"
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Game session duration in seconds"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", back_populates="minigame_scores")

    __table_args__ = (
        Index("ix_minigame_scores_user_id", "user_id"),
        Index("ix_minigame_scores_score", "score"),
    )

    def __repr__(self) -> str:
        return f"<MinigameScore(id={self.id}, user_id={self.user_id}, score={self.score})>"
