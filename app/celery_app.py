"""Celery application configuration."""

import ssl
from celery import Celery

from app.config import get_settings

settings = get_settings()

# SSL configuration for rediss:// URLs (DigitalOcean Managed Valkey)
# Use dict format which is compatible with both broker and backend
ssl_options = {
    "ssl_cert_reqs": ssl.CERT_NONE,
    "ssl_check_hostname": False,
}

# Create Celery app
celery_app = Celery(
    "reelin",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.leaderboard", "app.tasks.billing", "app.tasks.notifications", "app.tasks.achievements", "app.tasks.content_moderation", "app.tasks.ai_analysis"],
    broker_use_ssl=ssl_options if settings.celery_broker_url.startswith("rediss://") else None,
    redis_backend_use_ssl=ssl_options if settings.celery_result_backend.startswith("rediss://") else None,
)

# Celery configuration
celery_app.conf.update(
    # Connection settings
    broker_connection_retry_on_startup=True,

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

    # Worker memory management - prevent memory leaks from accumulating
    # Restart worker after 50 tasks to release memory more aggressively
    worker_max_tasks_per_child=50,
    # Restart worker if memory exceeds 1GB (container has 2GB, leave headroom)
    worker_max_memory_per_child=1000000,  # in KB (1GB)

    # Rate limiting
    task_annotations={
        "app.tasks.leaderboard.recalculate_event_leaderboard": {
            "rate_limit": "10/s",  # Max 10 recalculations per second
        },
    },

    # Result expiration (1 hour)
    result_expires=3600,
)
