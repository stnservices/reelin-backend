"""Lightweight audit service — no external API calls, safe for request path."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog, UserDevice

logger = logging.getLogger(__name__)


def parse_user_agent(ua_string: str) -> dict:
    """Parse a user-agent string into structured browser/OS/device info."""
    try:
        # Handle known non-browser clients before ua-parser
        # Dart HTTP client: "Dart/3.10 (dart:io)"
        dart_match = re.match(r"Dart/(\d+\.\d+)", ua_string)
        if dart_match:
            return {
                "browser_name": "Dart",
                "browser_version": dart_match.group(1),
                "os_name": "Dart VM",
                "os_version": dart_match.group(1),
                "device_type": "mobile",
            }

        from ua_parser import parse as ua_parse
        result = ua_parse(ua_string)

        # Browser info
        ua = result.user_agent
        browser_name = ua.family if ua else "Unknown"
        browser_version = ""
        if ua and ua.major:
            browser_version = ua.major
            if ua.minor:
                browser_version += f".{ua.minor}"

        # OS info
        os_info = result.os
        os_name = os_info.family if os_info else "Unknown"
        os_version = ""
        if os_info and os_info.major:
            os_version = os_info.major
            if os_info.minor:
                os_version += f".{os_info.minor}"

        # Device info
        device = result.device
        device_family = (device.family or "").lower() if device else ""

        # Determine device type
        if "spider" in browser_name.lower() or "bot" in browser_name.lower() or "crawl" in browser_name.lower():
            device_type = "bot"
        elif device_family in ("iphone", "ipod") or ("mobile" in ua_string.lower() and "tablet" not in ua_string.lower()):
            device_type = "mobile"
        elif device_family == "ipad" or "tablet" in ua_string.lower():
            device_type = "tablet"
        elif os_name in ("Windows", "Mac OS X", "Linux", "Chrome OS", "Ubuntu"):
            device_type = "desktop"
        elif device_family != "other" and device_family:
            device_type = "mobile"
        else:
            device_type = "desktop"

        return {
            "browser_name": browser_name,
            "browser_version": browser_version,
            "os_name": os_name,
            "os_version": os_version,
            "device_type": device_type,
        }
    except Exception:
        return {
            "browser_name": "Unknown",
            "browser_version": "",
            "os_name": "Unknown",
            "os_version": "",
            "device_type": "unknown",
        }


def normalize_email(email: str) -> str:
    """Normalize an email for comparison.

    Gmail: strip dots and +aliases from local part, lowercase.
    Others: strip +aliases from local part, lowercase.
    """
    email = email.strip().lower()
    local, domain = email.rsplit("@", 1)

    # Strip +alias
    local = local.split("+")[0]

    # Gmail-specific: strip dots
    gmail_domains = {"gmail.com", "googlemail.com"}
    if domain in gmail_domains:
        local = local.replace(".", "")

    return f"{local}@{domain}"


def extract_request_context(request: Request) -> dict:
    """Pull IP, User-Agent, X-Device-Id, X-Device-Info from a FastAPI Request."""
    # Real IP: check forwarded headers (DO App Platform sets X-Forwarded-For)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None

    user_agent = request.headers.get("user-agent")
    device_id = request.headers.get("x-device-id")

    # Parse device info JSON header
    device_info_raw = request.headers.get("x-device-info")
    device_info = None
    if device_info_raw:
        try:
            device_info = json.loads(device_info_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "ip": ip,
        "user_agent": user_agent,
        "device_id": device_id,
        "device_info": device_info,
    }


def log_event(
    db: AsyncSession,
    event_type: str,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
    device_info: Optional[dict] = None,
    details: Optional[dict] = None,
    risk_level: str = "low",
    success: Optional[bool] = None,
) -> AuditLog:
    """Create an AuditLog row. Does NOT commit — caller commits."""
    if details is None:
        details = {}

    # Parse user-agent if provided
    if user_agent:
        details["parsed_ua"] = parse_user_agent(user_agent)

    # Store structured device info from X-Device-Info header
    if device_info:
        details["device_info"] = device_info

    # Store success flag
    if success is not None:
        details["success"] = success

    entry = AuditLog(
        user_id=user_id,
        event_type=event_type,
        risk_level=risk_level,
        ip_address=ip,
        user_agent=user_agent,
        device_id=device_id,
        details=details if details else None,
    )
    db.add(entry)
    return entry


async def register_or_update_device(
    db: AsyncSession,
    user_id: int,
    device_id: str,
    ip: Optional[str] = None,
    device_info: Optional[dict] = None,
) -> tuple:
    """Upsert a UserDevice row. Returns (device, is_new)."""
    query = select(UserDevice).where(
        UserDevice.device_id == device_id,
        UserDevice.user_id == user_id,
    )
    result = await db.execute(query)
    device = result.scalar_one_or_none()

    os_name = device_info.get("os") if device_info else None
    os_version = device_info.get("version") if device_info else None
    brand = device_info.get("brand") if device_info else None
    model = device_info.get("model") if device_info else None
    device_name = f"{brand} {model}".strip() if brand or model else None

    if device:
        # Update existing
        device.last_seen_at = datetime.now(timezone.utc)
        if ip:
            device.last_ip = ip
        if os_name:
            device.os = os_name
        if os_version:
            device.os_version = os_version
        if brand:
            device.brand = brand
        if model:
            device.model = model
        if device_name:
            device.device_name = device_name
        return device, False
    else:
        # Create new
        device = UserDevice(
            user_id=user_id,
            device_id=device_id,
            device_name=device_name,
            os=os_name,
            os_version=os_version,
            brand=brand,
            model=model,
            first_ip=ip,
            last_ip=ip,
        )
        db.add(device)
        return device, True
