"""News model for platform news and announcements."""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount


class News(Base):
    """
    News articles for the public landing page.

    Organizers and admins can create news articles that are
    displayed publicly on the landing page when published.
    """

    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Content
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown content
    excerpt: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Summary for cards

    # Media
    featured_image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Ownership
    created_by_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )

    # Status
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )

    # Relationships
    created_by: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")

    def __repr__(self) -> str:
        return f"<News(id={self.id}, title={self.title[:30]}...)>"
