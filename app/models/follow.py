"""User follow system models."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, CheckConstraint, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserFollow(Base):
    """
    Represents a follow relationship between users.

    A user (follower) follows another user (following).
    """

    __tablename__ = "user_follows"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # The user who is following
    follower_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The user being followed
    following_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    follower: Mapped["UserAccount"] = relationship(
        "UserAccount",
        foreign_keys=[follower_id],
        lazy="joined",
    )
    following: Mapped["UserAccount"] = relationship(
        "UserAccount",
        foreign_keys=[following_id],
        lazy="joined",
    )

    __table_args__ = (
        # Each follow relationship must be unique
        UniqueConstraint("follower_id", "following_id", name="uq_user_follows_follower_following"),
        # Users cannot follow themselves
        CheckConstraint("follower_id != following_id", name="ck_user_follows_no_self_follow"),
        # Indexes for efficient lookups
        Index("ix_user_follows_follower_id", "follower_id"),
        Index("ix_user_follows_following_id", "following_id"),
    )

    def __repr__(self) -> str:
        return f"<UserFollow(follower_id={self.follower_id}, following_id={self.following_id})>"


# Import for type hints
from app.models.user import UserAccount
