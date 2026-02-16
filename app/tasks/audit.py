"""Celery tasks for repeat-offender detection and audit log enrichment."""

import logging
import time
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import sync_engine
from app.models.audit import AuditLog, UserDevice, UserSuspiciousFlag
from app.models.user import UserAccount
from app.services.audit_service import normalize_email, parse_user_agent

logger = logging.getLogger(__name__)


def _get_sync_session() -> Session:
    """Create a sync session for Celery tasks."""
    from sqlalchemy.orm import Session as SyncSession
    return SyncSession(bind=sync_engine, expire_on_commit=False)


@celery_app.task(bind=True, max_retries=3, soft_time_limit=60)
def check_repeat_offender(
    self,
    user_id: int,
    device_id: Optional[str],
    ip_address: Optional[str],
    email: Optional[str],
):
    """Check if a newly registered / new-device user matches any banned account.

    Runs async via Celery so login/registration is never delayed.
    Uses sync DB session (standard Celery pattern).
    """
    session = _get_sync_session()
    try:
        matches = []
        total_score = 0

        # 1. Device match (50 points)
        if device_id:
            device_matches = (
                session.execute(
                    select(UserDevice.user_id, UserAccount.email)
                    .join(UserAccount, UserAccount.id == UserDevice.user_id)
                    .where(
                        UserDevice.device_id == device_id,
                        UserDevice.user_id != user_id,
                        UserAccount.is_banned == True,
                    )
                )
                .all()
            )
            for banned_uid, banned_email in device_matches:
                matches.append({
                    "type": "device_id",
                    "banned_user_id": banned_uid,
                    "banned_email": banned_email,
                    "device_id": device_id,
                })
                total_score += 50

        # 2. IP match (25 points)
        if ip_address:
            # Check user_devices first_ip/last_ip
            ip_device_matches = (
                session.execute(
                    select(UserDevice.user_id, UserAccount.email)
                    .join(UserAccount, UserAccount.id == UserDevice.user_id)
                    .where(
                        or_(
                            UserDevice.first_ip == ip_address,
                            UserDevice.last_ip == ip_address,
                        ),
                        UserDevice.user_id != user_id,
                        UserAccount.is_banned == True,
                    )
                )
                .all()
            )
            # Also check audit_logs registration IP
            ip_audit_matches = (
                session.execute(
                    select(AuditLog.user_id, UserAccount.email)
                    .join(UserAccount, UserAccount.id == AuditLog.user_id)
                    .where(
                        AuditLog.ip_address == ip_address,
                        AuditLog.event_type == "registration",
                        AuditLog.user_id != user_id,
                        UserAccount.is_banned == True,
                    )
                )
                .all()
            )
            seen_ip_users = set()
            for banned_uid, banned_email in list(ip_device_matches) + list(ip_audit_matches):
                if banned_uid not in seen_ip_users:
                    seen_ip_users.add(banned_uid)
                    matches.append({
                        "type": "ip_address",
                        "banned_user_id": banned_uid,
                        "banned_email": banned_email,
                        "ip_address": ip_address,
                    })
                    total_score += 25

        # 3. Email match (25 points)
        if email:
            norm = normalize_email(email)
            email_matches = (
                session.execute(
                    select(UserAccount.id, UserAccount.email)
                    .where(
                        UserAccount.normalized_email == norm,
                        UserAccount.id != user_id,
                        UserAccount.is_banned == True,
                    )
                )
                .all()
            )
            for banned_uid, banned_email in email_matches:
                matches.append({
                    "type": "email_pattern",
                    "banned_user_id": banned_uid,
                    "banned_email": banned_email,
                    "normalized_email": norm,
                })
                total_score += 25

        if not matches:
            return {"status": "clean", "user_id": user_id}

        # Group matches by banned user
        banned_user_groups: dict[int, list[dict]] = {}
        for m in matches:
            buid = m["banned_user_id"]
            banned_user_groups.setdefault(buid, []).append(m)

        flags_created = 0
        for banned_uid, match_list in banned_user_groups.items():
            match_types = list({m["type"] for m in match_list})
            score = sum(
                50 if m["type"] == "device_id" else 25
                for m in match_list
            )
            score = min(score, 100)

            # Check if flag already exists
            existing = session.execute(
                select(UserSuspiciousFlag.id).where(
                    UserSuspiciousFlag.flagged_user_id == user_id,
                    UserSuspiciousFlag.matched_banned_user_id == banned_uid,
                )
            ).scalar_one_or_none()

            if existing:
                continue

            flag = UserSuspiciousFlag(
                flagged_user_id=user_id,
                matched_banned_user_id=banned_uid,
                match_types=match_types,
                match_details={"matches": match_list},
                risk_score=score,
                status="pending",
            )
            session.add(flag)
            flags_created += 1

        if flags_created > 0:
            session.commit()
            logger.warning(
                f"Repeat offender detected: user_id={user_id}, "
                f"flags_created={flags_created}, total_score={total_score}"
            )

        return {
            "status": "flagged" if flags_created > 0 else "already_flagged",
            "user_id": user_id,
            "flags_created": flags_created,
            "total_score": total_score,
        }

    except Exception as exc:
        session.rollback()
        logger.error(f"check_repeat_offender failed for user_id={user_id}: {exc}")
        raise self.retry(exc=exc, countdown=10)
    finally:
        session.close()


@celery_app.task(bind=True, max_retries=2, soft_time_limit=30)
def enrich_audit_log(self, audit_log_id: int):
    """Enrich a single audit log with geo data from ip-api.com.

    Fired after each audit event. Skips if already enriched or private IP.
    """
    from app.services.geo_enrichment import enrich_ips_batch

    session = _get_sync_session()
    try:
        log = session.execute(
            select(AuditLog).where(AuditLog.id == audit_log_id)
        ).scalar_one_or_none()

        if not log:
            return {"status": "not_found", "id": audit_log_id}

        details = dict(log.details) if log.details else {}

        # Skip if already enriched
        if details.get("enrichment"):
            return {"status": "already_enriched", "id": audit_log_id}

        ip = str(log.ip_address) if log.ip_address else None
        if not ip:
            return {"status": "no_ip", "id": audit_log_id}

        # Backfill parsed_ua if missing
        if not details.get("parsed_ua") and log.user_agent:
            details["parsed_ua"] = parse_user_agent(log.user_agent)

        # Geo enrich
        enrichment_map = enrich_ips_batch([ip])
        enrichment = enrichment_map.get(ip)

        if not enrichment:
            return {"status": "private_or_failed", "id": audit_log_id}

        details["enrichment"] = enrichment

        # Compute risk_reasons
        risk_reasons = details.get("risk_reasons") or []
        if enrichment.get("is_vpn"):
            risk_reasons.append("vpn_or_proxy")

        # Check for new_country: compare to user's previous logins
        if log.user_id and enrichment.get("country_code"):
            prev_countries = set()
            prev_logs = session.execute(
                select(AuditLog)
                .where(
                    AuditLog.user_id == log.user_id,
                    AuditLog.id != log.id,
                    AuditLog.event_type.in_(["login", "registration"]),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(20)
            ).scalars().all()
            for pl in prev_logs:
                pd = pl.details or {}
                pe = pd.get("enrichment") or {}
                cc = pe.get("country_code")
                if cc:
                    prev_countries.add(cc)
            if prev_countries and enrichment["country_code"] not in prev_countries:
                risk_reasons.append("new_country")

        if risk_reasons:
            details["risk_reasons"] = list(set(risk_reasons))

        # Upgrade risk_level if warranted
        current_risk = log.risk_level or "low"
        if "vpn_or_proxy" in risk_reasons and log.event_type in ("login", "login_failed"):
            if current_risk == "low":
                log.risk_level = "medium"
        if "new_country" in risk_reasons:
            if current_risk == "low":
                log.risk_level = "medium"

        log.details = details
        session.commit()

        return {"status": "enriched", "id": audit_log_id}

    except Exception as exc:
        session.rollback()
        logger.error(f"enrich_audit_log failed for id={audit_log_id}: {exc}")
        raise self.retry(exc=exc, countdown=15)
    finally:
        session.close()


@celery_app.task(bind=True, soft_time_limit=600)
def backfill_audit_enrichment(self, batch_size: int = 100, max_batches: int = 50):
    """One-time backfill: enrich existing audit logs that lack enrichment data.

    Processes in batches of up to 100 IPs (ip-api.com batch limit).
    Sleeps 1.5s between batches to respect rate limits (45 req/min).
    """
    from app.services.geo_enrichment import enrich_ips_batch

    session = _get_sync_session()
    total_enriched = 0
    try:
        for batch_num in range(max_batches):
            # Find logs without enrichment
            logs = session.execute(
                select(AuditLog)
                .where(
                    AuditLog.ip_address.isnot(None),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(batch_size)
                .offset(batch_num * batch_size)
            ).scalars().all()

            # Filter to only those without enrichment
            logs_to_enrich = [
                l for l in logs
                if not (l.details or {}).get("enrichment")
            ]

            if not logs_to_enrich:
                break

            # Collect unique IPs for batch request
            ip_set = set()
            for log in logs_to_enrich:
                if log.ip_address:
                    ip_set.add(str(log.ip_address))

            enrichment_map = enrich_ips_batch(list(ip_set)) if ip_set else {}

            for log in logs_to_enrich:
                details = dict(log.details) if log.details else {}
                ip = str(log.ip_address) if log.ip_address else None

                # Backfill parsed_ua
                if not details.get("parsed_ua") and log.user_agent:
                    details["parsed_ua"] = parse_user_agent(log.user_agent)

                # Add geo enrichment
                if ip and enrichment_map.get(ip):
                    details["enrichment"] = enrichment_map[ip]

                log.details = details
                total_enriched += 1

            session.commit()
            logger.info(f"Backfill batch {batch_num + 1}: enriched {len(logs_to_enrich)} logs")

            # Rate limit: sleep between batches
            time.sleep(1.5)

        return {"status": "complete", "total_enriched": total_enriched}

    except Exception as exc:
        session.rollback()
        logger.error(f"backfill_audit_enrichment failed: {exc}")
        raise
    finally:
        session.close()
