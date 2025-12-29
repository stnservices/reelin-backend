"""Organizer rules models for managing competition rules."""

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import UserAccount
    from app.models.event import EventType


class OrganizerRule(Base):
    """
    Organizer's competition rules that can be reused across events.
    Each organizer maintains their own private rule library.
    """

    __tablename__ = "organizer_rules"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Rule text content
    external_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Link to external rules
    document_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # Uploaded PDF/DOC document
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["UserAccount"] = relationship("UserAccount", lazy="joined")
    defaults: Mapped[list["OrganizerRuleDefault"]] = relationship(
        "OrganizerRuleDefault", back_populates="rule", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<OrganizerRule(id={self.id}, name={self.name}, owner_id={self.owner_id})>"


class OrganizerRuleDefault(Base):
    """
    Default rule assignments per Event Type for each organizer.
    When organizer creates an event of this type, the rule auto-applies.
    """

    __tablename__ = "organizer_rule_defaults"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type_id: Mapped[int] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("organizer_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Unique constraint: one default rule per Event Type per organizer
    __table_args__ = (
        UniqueConstraint("owner_id", "event_type_id", name="uq_organizer_rule_default"),
    )

    # Relationships
    rule: Mapped["OrganizerRule"] = relationship("OrganizerRule", back_populates="defaults")
    event_type: Mapped["EventType"] = relationship("EventType", lazy="joined")

    def __repr__(self) -> str:
        return f"<OrganizerRuleDefault(owner_id={self.owner_id}, event_type_id={self.event_type_id}, rule_id={self.rule_id})>"
