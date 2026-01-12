"""Profile picture moderation model for content safety."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ModerationStatus(str, Enum):
    """Profile picture moderation status."""

    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class RejectionReason(str, Enum):
    """Reasons for profile picture rejection."""

    ADULT_CONTENT = "adult_content"
    VIOLENT_CONTENT = "violent_content"
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    OFFENSIVE_GESTURE = "offensive_gesture"
    OFFENSIVE_TEXT = "offensive_text"
    API_ERROR = "api_error"


class ProfilePictureModeration(Base):
    """
    Audit log for profile picture content moderation.
    Tracks all moderation requests and their results.
    """

    __tablename__ = "profile_picture_moderation"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        default=ModerationStatus.PENDING.value,
        server_default="pending",
        nullable=False,
        index=True,
    )

    # SafeSearch scores (0-5 likelihood levels from Google Vision)
    # 0=UNKNOWN, 1=VERY_UNLIKELY, 2=UNLIKELY, 3=POSSIBLE, 4=LIKELY, 5=VERY_LIKELY
    adult_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    violence_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    racy_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Rejection info
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Processing info
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Enhanced detection results (labels, OCR text)
    detected_labels: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    detected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    offensive_labels_found: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    offensive_text_found: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    @property
    def is_approved(self) -> bool:
        return self.status == ModerationStatus.APPROVED.value

    @property
    def is_rejected(self) -> bool:
        return self.status == ModerationStatus.REJECTED.value

    @property
    def is_pending(self) -> bool:
        return self.status in (
            ModerationStatus.PENDING.value,
            ModerationStatus.PROCESSING.value,
        )

    def __repr__(self) -> str:
        return f"<ProfilePictureModeration(id={self.id}, user_id={self.user_id}, status={self.status})>"


# Import for type hints
from app.models.user import UserAccount
