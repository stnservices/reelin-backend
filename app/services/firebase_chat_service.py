"""Firebase Realtime Database chat sync service."""

import logging
from datetime import datetime
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
        logger.warning("Firebase Admin SDK not initialized, chat sync disabled")
        return False

    # Check if database URL is configured
    settings = get_settings()
    if not settings.firebase_database_url:
        logger.warning("FIREBASE_DATABASE_URL not set, chat sync disabled")
        return False

    return True


def sync_chat_message(
    event_id: int,
    message_id: int,
    user_id: int,
    user_name: str,
    user_avatar: Optional[str],
    is_organizer: bool,
    message: str,
    message_type: str,
    is_pinned: bool,
    created_at: datetime,
) -> bool:
    """Sync a chat message to Firebase Realtime Database.

    Args:
        event_id: The event ID
        message_id: The message ID
        user_id: The user ID who sent the message
        user_name: The user's display name
        user_avatar: The user's avatar URL
        is_organizer: Whether the user is the event organizer
        message: The message content
        message_type: The message type (message, announcement)
        is_pinned: Whether the message is pinned
        created_at: When the message was created

    Returns:
        True if synced successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'chat/{event_id}/messages/{message_id}')
        ref.set({
            'id': message_id,
            'event_id': event_id,
            'user_id': user_id,
            'user_name': user_name,
            'user_avatar': user_avatar,
            'is_organizer': is_organizer,
            'message': message,
            'message_type': message_type,
            'is_pinned': is_pinned,
            'created_at': created_at.isoformat(),
        })
        logger.debug(f"Chat message {message_id} synced to Firebase for event {event_id}")
        return True

    except Exception as e:
        logger.error(f"Error syncing chat message to Firebase: {e}")
        return False


def delete_chat_message(event_id: int, message_id: int) -> bool:
    """Delete a chat message from Firebase Realtime Database.

    Args:
        event_id: The event ID
        message_id: The message ID to delete

    Returns:
        True if deleted successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'chat/{event_id}/messages/{message_id}')
        ref.delete()
        logger.debug(f"Chat message {message_id} deleted from Firebase for event {event_id}")
        return True

    except Exception as e:
        logger.error(f"Error deleting chat message from Firebase: {e}")
        return False


def update_message_pinned(event_id: int, message_id: int, is_pinned: bool, pinned_at: Optional[datetime] = None) -> bool:
    """Update pinned status of a message in Firebase.

    Args:
        event_id: The event ID
        message_id: The message ID
        is_pinned: Whether the message is pinned
        pinned_at: When the message was pinned (if pinned)

    Returns:
        True if updated successfully, False otherwise.
    """
    if not _ensure_firebase_ready():
        return False

    try:
        ref = firebase_db.reference(f'chat/{event_id}/messages/{message_id}')
        updates = {
            'is_pinned': is_pinned,
            'pinned_at': pinned_at.isoformat() if pinned_at else None,
        }
        ref.update(updates)
        logger.debug(f"Chat message {message_id} pin status updated in Firebase: {is_pinned}")
        return True

    except Exception as e:
        logger.error(f"Error updating chat message pin status in Firebase: {e}")
        return False
