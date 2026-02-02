"""Firebase Realtime Database leaderboard sync service.

Syncs leaderboard data to Firebase for real-time web updates.
Includes presence-based viewer counting for accurate viewer numbers.
"""

import logging
import time
from typing import Optional

import firebase_admin
from firebase_admin import db as firebase_db

from app.config import get_settings
from app.services.push_notifications import initialize_firebase

logger = logging.getLogger(__name__)


def _ensure_firebase_ready() -> bool:
    """Ensure Firebase is initialized with Realtime Database support.

    Returns:
        True if ready, False otherwise.
    """
    # Initialize Firebase if needed
    if not initialize_firebase():
        logger.warning("Firebase Admin SDK not initialized, leaderboard sync disabled")
        return False

    # Check if database URL is configured
    settings = get_settings()
    if not settings.firebase_database_url:
        logger.warning("FIREBASE_DATABASE_URL not set, leaderboard sync disabled")
        return False

    return True


def sync_leaderboard_to_firebase(
    event_id: int,
    leaderboard_data: dict,
    movements: list,
    recent_catches: Optional[list] = None,
) -> bool:
    """Sync leaderboard data to Firebase Realtime Database.

    This writes:
    - /events/{event_id}/leaderboard - Full leaderboard data (no images)
    - /events/{event_id}/movements - Recent ranking movements
    - /events/{event_id}/recentCatches - Last 20 validated catches (no images)

    Args:
        event_id: The event ID
        leaderboard_data: Full leaderboard dict from calculation
        movements: List of recent ranking movements
        recent_catches: Optional list of recent catches to sync

    Returns:
        True if synced successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        logger.warning(f"Firebase not ready, skipping leaderboard sync for event {event_id}")
        return False

    try:
        logger.info(f"Syncing leaderboard to Firebase for event {event_id} ({len(leaderboard_data.get('entries', []))} entries)")
        ref = firebase_db.reference(f'events/{event_id}')
        now_ms = int(time.time() * 1000)

        # Build leaderboard entries (WITHOUT images to reduce bandwidth)
        firebase_entries = []
        for entry in leaderboard_data.get("entries", []):
            firebase_entry = {
                "rank": entry.get("rank"),
                "totalPoints": entry.get("total_points", 0),
                "catchCount": entry.get("total_catches", 0),
                "countedCatches": entry.get("counted_catches", 0),
                "speciesCount": entry.get("species_count", 0),
                "speciesBonus": entry.get("species_bonus", 0),
                "bestCatchLength": entry.get("best_catch_length"),
                "bestCatchSpecies": entry.get("best_catch_species"),
                "averageCatch": entry.get("average_catch"),
                "isDisqualified": entry.get("is_disqualified", False),
            }

            # Add user or team info (NO avatar/logo URLs)
            if entry.get("user_id"):
                firebase_entry["odUserId"] = entry["user_id"]
                firebase_entry["displayName"] = entry.get("user_name", "")
            if entry.get("team_id"):
                firebase_entry["odTeamId"] = entry["team_id"]
                firebase_entry["displayName"] = entry.get("team_name", "")

            firebase_entries.append(firebase_entry)

        # Build Firebase leaderboard object
        firebase_leaderboard = {
            "lastUpdated": now_ms,
            "totalCatches": leaderboard_data.get("total_catches", 0),
            "totalParticipants": leaderboard_data.get("total_participants", 0),
            "totalTeams": leaderboard_data.get("total_teams"),
            "isTeamEvent": leaderboard_data.get("is_team_event", False),
            "scoringType": leaderboard_data.get("scoring_type", ""),
            "eventName": leaderboard_data.get("event_name", ""),
            "entries": firebase_entries,
        }

        # Write leaderboard
        ref.child("leaderboard").set(firebase_leaderboard)
        logger.info(f"Leaderboard synced to Firebase for event {event_id}")

        # Write movements (keep last 10)
        if movements:
            movements_data = {}
            for m in movements[:10]:
                movement_id = str(m.get("id", int(time.time() * 1000)))
                movements_data[movement_id] = {
                    "odUserId": m.get("user_id"),
                    "odTeamId": m.get("team_id"),
                    "displayName": m.get("user_name") or m.get("team_name", ""),
                    "oldRank": m.get("old_rank"),
                    "newRank": m.get("new_rank"),
                    "movement": m.get("movement", 0),
                    "catchFish": m.get("catch_fish"),
                    "catchLength": m.get("catch_length"),
                    "createdAt": now_ms,
                }
            ref.child("movements").set(movements_data)

        # Write recent catches if provided (keep last 20, NO photo URLs)
        if recent_catches:
            catches_data = {}
            for c in recent_catches[:20]:
                catch_id = str(c.get("catch_id", c.get("id")))
                catches_data[catch_id] = {
                    "fishName": c.get("fish_name", ""),
                    "length": c.get("length", 0),
                    "points": c.get("points", 0),
                    "anglerName": c.get("angler_name", c.get("user_name", "")),
                    "validatedAt": c.get("validated_at_ms", now_ms),
                    "isScored": c.get("is_scored", True),
                    # NO photo_url - loaded separately if needed
                }
            ref.child("recentCatches").set(catches_data)

        return True

    except Exception as e:
        logger.error(f"Error syncing leaderboard to Firebase for event {event_id}: {e}")
        return False


def sync_catch_validated(
    event_id: int,
    catch_data: dict,
) -> bool:
    """Sync a newly validated catch to Firebase.

    Called when a catch is validated to update recentCatches in real-time.

    Args:
        event_id: The event ID
        catch_data: Catch details dict

    Returns:
        True if synced successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'events/{event_id}/recentCatches')
        now_ms = int(time.time() * 1000)

        catch_id = str(catch_data.get("catch_id", catch_data.get("id")))

        ref.child(catch_id).set({
            "fishName": catch_data.get("fish_name", ""),
            "length": catch_data.get("length", 0),
            "points": catch_data.get("points", 0),
            "anglerName": catch_data.get("angler_name", catch_data.get("user_name", "")),
            "validatedAt": now_ms,
            "isScored": catch_data.get("is_scored", True),
        })

        logger.debug(f"Catch {catch_id} synced to Firebase for event {event_id}")
        return True

    except Exception as e:
        logger.error(f"Error syncing catch to Firebase for event {event_id}: {e}")
        return False


def get_viewer_count(event_id: int) -> int:
    """Get accurate viewer count from Firebase presence.

    Viewers write to /events/{event_id}/presence/{viewerId} = true
    and set onDisconnect to remove their entry.

    Args:
        event_id: The event ID

    Returns:
        Number of active viewers, or 0 if error.
    """
    if not _ensure_firebase_ready():
        return 0

    try:
        ref = firebase_db.reference(f'events/{event_id}/presence')
        presence_data = ref.get()

        if presence_data and isinstance(presence_data, dict):
            return len(presence_data)
        return 0

    except Exception as e:
        logger.error(f"Error getting viewer count from Firebase for event {event_id}: {e}")
        return 0


def sync_ta_standings_to_firebase(
    event_id: int,
    standings: list,
    current_phase: str,
    current_leg: int,
    total_legs: int,
    completed_legs: int,
    has_knockout_bracket: bool = False,
    bracket_data: Optional[dict] = None,
) -> bool:
    """Sync Trout Area standings to Firebase Realtime Database.

    Writes to /events/{event_id}/taStandings for TA live view.

    Args:
        event_id: The event ID
        standings: List of TAStanding dicts
        current_phase: Current phase (qualifier, semifinal, etc.)
        current_leg: Current leg number
        total_legs: Total legs in event
        completed_legs: Number of completed legs
        has_knockout_bracket: Whether knockout bracket exists
        bracket_data: Optional bracket data dict

    Returns:
        True if synced successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'events/{event_id}')
        now_ms = int(time.time() * 1000)

        # Build standings entries (NO avatar URLs)
        firebase_standings = []
        for entry in standings:
            firebase_standings.append({
                "rank": entry.get("rank"),
                "odUserId": entry.get("user_id"),
                "displayName": entry.get("display_name", ""),
                "points": entry.get("points", 0),
                "totalCatches": entry.get("total_catches", 0),
                "victories": entry.get("victories", 0),
                "ties": entry.get("ties", 0),
                "losses": entry.get("losses", 0),
                "positionChange": entry.get("position_change", 0),
            })

        # Write TA standings data
        ta_data = {
            "lastUpdated": now_ms,
            "currentPhase": current_phase,
            "currentLeg": current_leg,
            "totalLegs": total_legs,
            "completedLegs": completed_legs,
            "hasKnockoutBracket": has_knockout_bracket,
            "standings": firebase_standings,
        }

        ref.child("taStandings").set(ta_data)
        logger.debug(f"TA standings synced to Firebase for event {event_id}")

        # Write bracket data if provided
        if bracket_data:
            ref.child("taBracket").set({
                "lastUpdated": now_ms,
                **bracket_data,
            })
            logger.debug(f"TA bracket synced to Firebase for event {event_id}")

        return True

    except Exception as e:
        logger.error(f"Error syncing TA standings to Firebase for event {event_id}: {e}")
        return False


def sync_validator_event(
    event_id: int,
    event_type: str,
    data: dict,
) -> bool:
    """Sync a validator event to Firebase for real-time updates.

    Events are written to /events/{event_id}/validatorEvents/{timestamp}
    and auto-expire after being read.

    Args:
        event_id: The event ID
        event_type: Event type (catch_submitted, catch_validated, ai_analysis_complete)
        data: Event data dict

    Returns:
        True if synced successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'events/{event_id}/validatorEvents')
        now_ms = int(time.time() * 1000)

        # Write event with timestamp as key for ordering
        ref.child(str(now_ms)).set({
            "type": event_type,
            "timestamp": now_ms,
            **data,
        })

        logger.debug(f"Validator event {event_type} synced to Firebase for event {event_id}")
        return True

    except Exception as e:
        logger.error(f"Error syncing validator event to Firebase for event {event_id}: {e}")
        return False


def cleanup_event_data(event_id: int) -> bool:
    """Clean up Firebase data for an event.

    Called when event ends to remove real-time data.

    Args:
        event_id: The event ID

    Returns:
        True if cleaned up successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'events/{event_id}')

        # Remove real-time data (keep leaderboard for reference)
        ref.child("movements").delete()
        ref.child("recentCatches").delete()
        ref.child("presence").delete()
        ref.child("taStandings").delete()
        ref.child("taBracket").delete()

        logger.info(f"Firebase real-time data cleaned up for event {event_id}")
        return True

    except Exception as e:
        logger.error(f"Error cleaning up Firebase data for event {event_id}: {e}")
        return False
