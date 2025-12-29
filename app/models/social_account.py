"""Social account models for OAuth authentication."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OAuthProvider(str, Enum):
    """Supported OAuth providers."""
    GOOGLE = "google"
    FACEBOOK = "facebook"
    APPLE = "apple"


# Create PostgreSQL enum type
oauth_provider_enum = ENUM(
    OAuthProvider,
    name="oauth_provider",
    create_constraint=True,
    metadata=Base.metadata,
    validate_strings=True,
)


class SocialAccount(Base):
    """
    Social account linked to a user for OAuth authentication.

    A user can have multiple social accounts (e.g., both Google and Facebook).
    Each provider + provider_account_id combination must be unique.
    """

    __tablename__ = "social_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        oauth_provider_enum,
        nullable=False,
    )
    provider_account_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # OAuth tokens (encrypted at rest in production)
    access_token: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship(
        "UserAccount",
        back_populates="social_accounts",
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_account_id",
            name="uq_social_provider_account",
        ),
    )

    def __repr__(self) -> str:
        return f"<SocialAccount(id={self.id}, provider={self.provider}, user_id={self.user_id})>"


# Import for type hints (avoid circular imports)
from app.models.user import UserAccount
