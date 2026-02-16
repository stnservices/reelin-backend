"""Geo-enrichment service using ip-api.com free tier (no API key required)."""

import ipaddress
import logging

import httpx

logger = logging.getLogger(__name__)

# ip-api.com batch endpoint: POST up to 100 IPs, 45 req/min on free tier
BATCH_URL = "http://ip-api.com/batch"
FIELDS = "status,country,countryCode,regionName,city,isp,proxy,hosting"
TIMEOUT = 10.0


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/reserved."""
    try:
        return ipaddress.ip_address(ip).is_private
    except (ValueError, TypeError):
        return True


def enrich_ips_batch(ips: list[str]) -> dict[str, dict | None]:
    """POST to ip-api.com batch endpoint. Returns {ip: enrichment_dict | None}.

    Each enrichment_dict has:
        country, country_code, city, region, isp, is_vpn
    """
    # Filter out private/invalid IPs
    valid_ips = [ip for ip in set(ips) if not _is_private_ip(ip)]
    if not valid_ips:
        return {ip: None for ip in ips}

    # Build request body: list of {"query": ip, "fields": fields}
    request_body = [{"query": ip, "fields": FIELDS} for ip in valid_ips]

    result: dict[str, dict | None] = {ip: None for ip in ips}

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(BATCH_URL, json=request_body)
            resp.raise_for_status()
            data = resp.json()

        for item in data:
            ip = item.get("query")
            if not ip or item.get("status") != "success":
                continue
            result[ip] = {
                "country": item.get("country"),
                "country_code": item.get("countryCode"),
                "city": item.get("city"),
                "region": item.get("regionName"),
                "isp": item.get("isp"),
                "is_vpn": bool(item.get("proxy") or item.get("hosting")),
            }
    except Exception as exc:
        logger.warning(f"ip-api.com batch request failed: {exc}")

    return result
