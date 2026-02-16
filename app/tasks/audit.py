"""Celery task for repeat-offender detection after registration / new-device login."""

import logging
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import sync_engine
from app.models.audit import AuditLog, UserDevice, UserSuspiciousFlag
from app.models.user import UserAccount
from app.services.audit_service import normalize_email

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
