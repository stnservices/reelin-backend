"""Geo-enrichment service using ip-api.com free tier (no API key required)."""

import ipaddress
import logging

import httpx

logger = logging.getLogger(__name__)

# ip-api.com batch endpoint: POST up to 100 IPs, 45 req/min on free tier
BATCH_URL = "http://ip-api.com/batch"
FIELDS = "status,country,countryCode,regionName,city,isp,proxy,hosting"
TIMEOUT = 10.0


def _clean_ip(ip: str) -> str:
    """Strip CIDR suffix and whitespace (PostgreSQL INET can return '1.2.3.4/32')."""
    return ip.split("/")[0].strip()


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/reserved."""
    try:
        return ipaddress.ip_address(_clean_ip(ip)).is_private
    except (ValueError, TypeError):
        return True


def enrich_ips_batch(ips: list[str]) -> dict[str, dict | None]:
    """POST to ip-api.com batch endpoint. Returns {ip: enrichment_dict | None}.

    Accepts raw IPs (may include CIDR suffix from PostgreSQL INET).
    Returns dict keyed by the ORIGINAL ip strings passed in.

    Each enrichment_dict has:
        country, country_code, city, region, isp, is_vpn
    """
    # Build mapping: clean_ip -> list of original ip strings
    clean_to_orig: dict[str, list[str]] = {}
    for ip in ips:
        clean = _clean_ip(ip)
        clean_to_orig.setdefault(clean, []).append(ip)

    # Filter out private/invalid IPs
    valid_clean_ips = [cip for cip in clean_to_orig if not _is_private_ip(cip)]
    result: dict[str, dict | None] = {ip: None for ip in ips}

    if not valid_clean_ips:
        return result

    # Build request body: list of {"query": ip, "fields": fields}
    request_body = [{"query": ip, "fields": FIELDS} for ip in valid_clean_ips]

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(BATCH_URL, json=request_body)
            resp.raise_for_status()
            data = resp.json()

        for item in data:
            query_ip = item.get("query")
            if not query_ip or item.get("status") != "success":
                continue
            enrichment = {
                "country": item.get("country"),
                "country_code": item.get("countryCode"),
                "city": item.get("city"),
                "region": item.get("regionName"),
                "isp": item.get("isp"),
                "is_vpn": bool(item.get("proxy") or item.get("hosting")),
            }
            # Map back to all original IP strings for this clean IP
            for orig_ip in clean_to_orig.get(query_ip, []):
                result[orig_ip] = enrichment
    except Exception as exc:
        logger.warning(f"ip-api.com batch request failed: {exc}")

    return result
