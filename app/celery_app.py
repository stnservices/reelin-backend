"""Celery application configuration."""

from celery import Celery

from app.config import get_settings

settings = get_settings()

# Create Celery app
celery_app = Celery(
    "reelin",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.leaderboard", "app.tasks.billing", "app.tasks.notifications", "app.tasks.achievements", "app.tasks.content_moderation", "app.tasks.ai_analysis"],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Retry settings
    task_default_retry_delay=5,
    task_max_retries=3,

    # Rate limiting
    task_annotations={
        "app.tasks.leaderboard.recalculate_event_leaderboard": {
            "rate_limit": "10/s",  # Max 10 recalculations per second
        },
    },

    # Result expiration (1 hour)
    result_expires=3600,
)
