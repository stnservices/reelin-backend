"""Audit logging, device tracking, and suspicious flag models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AuditLog(Base):
    """One row per auditable event (registration, login, ban, etc.)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_event_type", "event_type"),
        Index("ix_audit_logs_device_id", "device_id"),
        Index("ix_audit_logs_ip_address", "ip_address"),
        Index("ix_audit_logs_created_at_desc", "created_at", postgresql_using="btree"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, event={self.event_type}, user={self.user_id})>"


class UserDevice(Base):
    """Physical devices per user (NOT FCM tokens)."""

    __tablename__ = "user_devices"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    device_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    os: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    os_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    first_ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    last_ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)

    user = relationship("UserAccount", lazy="noload")

    __table_args__ = (
        UniqueConstraint("device_id", "user_id", name="uq_user_device"),
        Index("ix_user_devices_device_id", "device_id"),
        Index("ix_user_devices_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<UserDevice(id={self.id}, user={self.user_id}, device={self.device_id})>"


class UserSuspiciousFlag(Base):
    """Links a new account to a matched banned account."""

    __tablename__ = "user_suspicious_flags"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    flagged_user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    matched_banned_user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_types: Mapped[list] = mapped_column(JSONB, nullable=False)
    match_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    resolved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    flagged_user = relationship("UserAccount", foreign_keys=[flagged_user_id], lazy="joined")
    matched_banned_user = relationship("UserAccount", foreign_keys=[matched_banned_user_id], lazy="joined")
    resolved_by = relationship("UserAccount", foreign_keys=[resolved_by_id], lazy="noload")

    def __repr__(self) -> str:
        return f"<UserSuspiciousFlag(id={self.id}, flagged={self.flagged_user_id}, matched={self.matched_banned_user_id})>"
