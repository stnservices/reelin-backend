"""Catch reaction models for likes/dislikes on catch photos."""

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, CheckConstraint, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ReactionType(str, Enum):
    """Reaction type enum."""

    LIKE = "like"
    DISLIKE = "dislike"


class CatchReaction(Base):
    """
    Represents a user's reaction (like/dislike) to a catch photo.

    Each user can have at most one reaction per catch.
    Users cannot react to their own catches.
    """

    __tablename__ = "catch_reactions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    catch_id: Mapped[int] = mapped_column(
        ForeignKey("catches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reaction_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    catch: Mapped["Catch"] = relationship("Catch", lazy="joined")
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    __table_args__ = (
        # Each user can only react once per catch
        UniqueConstraint("catch_id", "user_id", name="uq_catch_reactions_catch_user"),
        # Indexes for efficient lookups
        Index("ix_catch_reactions_catch_id", "catch_id"),
        Index("ix_catch_reactions_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<CatchReaction(catch_id={self.catch_id}, user_id={self.user_id}, type={self.reaction_type})>"


# Import for type hints
from app.models.catch import Catch
from app.models.user import UserAccount
