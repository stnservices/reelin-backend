"""Firebase Cloud Messaging (FCM) push notification service."""

import json
import logging
from typing import List, Optional

import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin.exceptions import FirebaseError

from app.config import get_settings

logger = logging.getLogger(__name__)

# Track if Firebase has been initialized
_firebase_initialized = False


def initialize_firebase() -> bool:
    """Initialize Firebase Admin SDK.

    Returns:
        True if initialized successfully, False otherwise.
    """
    global _firebase_initialized

    if _firebase_initialized:
        return True

    settings = get_settings()

    if not settings.firebase_credentials:
        logger.warning("FIREBASE_CREDENTIALS not set, push notifications disabled")
        return False

    try:
        # Check if already initialized
        try:
            firebase_admin.get_app()
            _firebase_initialized = True
            return True
        except ValueError:
            pass

        # Parse credentials from JSON string
        firebase_credentials = json.loads(settings.firebase_credentials)
        cred = credentials.Certificate(firebase_credentials)

        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("Firebase Admin SDK initialized successfully (FCM only)")
        return True

    except json.JSONDecodeError as e:
        logger.error(f"Error parsing FIREBASE_CREDENTIALS_JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"Error initializing Firebase: {e}")
        return False


def send_push_notification(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
    click_action: Optional[str] = None,
) -> dict:
    """Send push notification to multiple FCM tokens.

    Args:
        tokens: List of FCM registration tokens
        title: Notification title
        body: Notification body
        data: Optional data payload
        click_action: Optional click action URL/route

    Returns:
        Dict with success_count, failure_count, and failed_tokens
    """
    if not tokens:
        logger.info("No tokens provided, skipping notification")
        return {"success_count": 0, "failure_count": 0, "failed_tokens": []}

    if not initialize_firebase():
        logger.warning("Firebase not initialized, cannot send notifications")
        return {"success_count": 0, "failure_count": len(tokens), "failed_tokens": tokens}

    # Build data payload
    notification_data = data or {}
    if click_action:
        notification_data["click_action"] = click_action

    # Add title and body to data for Flutter handling
    notification_data["title"] = title
    notification_data["body"] = body

    # FCM has a 500 token limit per multicast
    batch_size = 500
    total_success = 0
    total_failure = 0
    all_failed_tokens = []

    for i in range(0, len(tokens), batch_size):
        batch_tokens = tokens[i:i + batch_size]

        try:
            # Create multicast message
            # Using data-only message for better Flutter handling
            multicast_message = messaging.MulticastMessage(
                data={k: str(v) for k, v in notification_data.items()},
                tokens=batch_tokens,
                # Android config for high priority
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        title=title,
                        body=body,
                        click_action=click_action,
                    ),
                ),
                # iOS/APNs config
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            alert=messaging.ApsAlert(
                                title=title,
                                body=body,
                            ),
                            sound="default",
                            badge=1,
                        ),
                    ),
                ),
            )

            # Send batch
            response = messaging.send_each_for_multicast(multicast_message)

            total_success += response.success_count
            total_failure += response.failure_count

            # Track failed tokens for cleanup
            if response.failure_count > 0:
                for idx, send_response in enumerate(response.responses):
                    if not send_response.success:
                        failed_token = batch_tokens[idx]
                        all_failed_tokens.append(failed_token)

                        # Log the error
                        if send_response.exception:
                            logger.warning(
                                f"Failed to send to token {failed_token[:20]}...: "
                                f"{type(send_response.exception).__name__}"
                            )

            logger.info(
                f"Batch {i // batch_size + 1}: "
                f"sent {response.success_count}, failed {response.failure_count}"
            )

        except FirebaseError as e:
            logger.error(f"Firebase error sending batch: {e}")
            total_failure += len(batch_tokens)
            all_failed_tokens.extend(batch_tokens)
        except Exception as e:
            logger.error(f"Unexpected error sending batch: {e}")
            total_failure += len(batch_tokens)
            all_failed_tokens.extend(batch_tokens)

    logger.info(
        f"Push notification complete - "
        f"Success: {total_success}, Failed: {total_failure}"
    )

    return {
        "success_count": total_success,
        "failure_count": total_failure,
        "failed_tokens": all_failed_tokens,
    }


def send_single_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    click_action: Optional[str] = None,
) -> bool:
    """Send push notification to a single token.

    Args:
        token: FCM registration token
        title: Notification title
        body: Notification body
        data: Optional data payload
        click_action: Optional click action URL/route

    Returns:
        True if sent successfully, False otherwise
    """
    if not initialize_firebase():
        return False

    notification_data = data or {}
    if click_action:
        notification_data["click_action"] = click_action
    notification_data["title"] = title
    notification_data["body"] = body

    try:
        message = messaging.Message(
            data={k: str(v) for k, v in notification_data.items()},
            token=token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    title=title,
                    body=body,
                    click_action=click_action,
                ),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(
                            title=title,
                            body=body,
                        ),
                        sound="default",
                    ),
                ),
            ),
        )

        response = messaging.send(message)
        logger.info(f"Successfully sent message: {response}")
        return True

    except messaging.UnregisteredError:
        logger.warning(f"Token {token[:20]}... is unregistered")
        return False
    except FirebaseError as e:
        logger.error(f"Firebase error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False


def send_silent_push(
    token: str,
    data: dict,
) -> bool:
    """Send silent/data-only push notification (no UI shown).

    Used to trigger background refresh in the app without showing
    any notification to the user. Works even if notifications are disabled.

    Args:
        token: FCM registration token
        data: Data payload to send

    Returns:
        True if sent successfully, False otherwise
    """
    if not initialize_firebase():
        return False

    try:
        # Data-only message - no notification payload = silent push
        message = messaging.Message(
            data={k: str(v) for k, v in data.items()},
            token=token,
            # Android: high priority to wake up app
            android=messaging.AndroidConfig(
                priority="high",
            ),
            # iOS: content-available for background processing
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        content_available=True,  # Silent push flag for iOS
                    ),
                ),
            ),
        )

        response = messaging.send(message)
        logger.info(f"Silent push sent successfully: {response}")
        return True

    except messaging.UnregisteredError:
        logger.warning(f"Token {token[:20]}... is unregistered")
        return False
    except FirebaseError as e:
        logger.error(f"Firebase error sending silent push: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending silent push: {e}")
        return False


def is_token_valid(token: str) -> bool:
    """Check if an FCM token is valid by sending a dry run.

    Args:
        token: FCM registration token to validate

    Returns:
        True if token is valid, False otherwise
    """
    if not initialize_firebase():
        return False

    try:
        message = messaging.Message(
            data={"validation": "true"},
            token=token,
        )
        # Dry run doesn't actually send the message
        messaging.send(message, dry_run=True)
        return True
    except messaging.UnregisteredError:
        return False
    except FirebaseError:
        return False
    except Exception:
        return False
