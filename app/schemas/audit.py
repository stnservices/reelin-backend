"""Pydantic v2 schemas for audit, device tracking, and suspicious flags."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Audit Logs ──

class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    event_type: str
    risk_level: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    device_id: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EnrichedAuditLogResponse(AuditLogResponse):
    """Audit log with flattened enrichment fields for admin display."""
    browser_name: Optional[str] = None
    browser_version: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    device_type: Optional[str] = None
    device_brand: Optional[str] = None
    device_model: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    isp: Optional[str] = None
    is_vpn: Optional[bool] = None
    success: Optional[bool] = None
    risk_reasons: Optional[list] = None


class AuditStatsResponse(BaseModel):
    events_today: int = 0
    registrations_today: int = 0
    logins_today: int = 0
    failed_logins_today: int = 0
    pending_flags: int = 0
    banned_users: int = 0


# ── User Devices ──

class UserDeviceResponse(BaseModel):
    id: int
    user_id: int
    device_id: str
    device_name: Optional[str] = None
    os: Optional[str] = None
    os_version: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    first_seen_at: datetime
    last_seen_at: datetime
    first_ip: Optional[str] = None
    last_ip: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Suspicious Flags ──

class SuspiciousFlagResponse(BaseModel):
    id: int
    flagged_user_id: int
    flagged_user_email: Optional[str] = None
    flagged_user_name: Optional[str] = None
    matched_banned_user_id: int
    matched_banned_user_email: Optional[str] = None
    matched_banned_user_name: Optional[str] = None
    match_types: list
    match_details: Optional[dict] = None
    risk_score: int
    status: str
    resolved_by_id: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SuspiciousFlagResolve(BaseModel):
    status: str = Field(..., pattern="^(confirmed|dismissed)$")
    resolution_note: Optional[str] = None


# ── Ban / Unban ──

class BanUserRequest(BaseModel):
    reason: str = Field(..., max_length=500)


class UnbanUserRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


# ── Paginated wrapper ──

class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class PaginatedEnrichedAuditLogs(BaseModel):
    items: list[EnrichedAuditLogResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class PaginatedSuspiciousFlags(BaseModel):
    items: list[SuspiciousFlagResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
