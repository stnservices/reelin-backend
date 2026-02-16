"""Celery application configuration."""

import os
import socket
import ssl

# Solo pool (-P solo) works with async code without patching

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
    include=["app.tasks.leaderboard", "app.tasks.billing", "app.tasks.notifications", "app.tasks.achievements", "app.tasks.achievement_processing", "app.tasks.content_moderation", "app.tasks.ai_analysis", "app.tasks.statistics", "app.tasks.audit"],
    broker_use_ssl=ssl_options if settings.celery_broker_url.startswith("rediss://") else None,
    redis_backend_use_ssl=ssl_options if settings.celery_result_backend.startswith("rediss://") else None,
)

# Celery configuration
celery_app.conf.update(
    # Connection settings (from Django config)
    broker_connection_retry_on_startup=True,
    broker_heartbeat=30,  # Connection health checks
    broker_pool_limit=None,  # Unlimited connections
    broker_transport_options={
        "visibility_timeout": 3600,  # 1 hour - task re-queue timeout
        "health_check_interval": 30,
        "socket_keepalive": True,
        "socket_keepalive_options": {
            socket.TCP_KEEPIDLE: 30,
            socket.TCP_KEEPINTVL: 10,
            socket.TCP_KEEPCNT: 3,
        },
        "retry_on_timeout": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },

    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=300,  # 5 min hard limit per task (from Django)
    task_soft_time_limit=240,  # 4 min soft limit - allows graceful shutdown

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

    # Connection loss behavior
    worker_cancel_long_running_tasks_on_connection_loss=True,

    # Result expiration (1 hour)
    result_expires=3600,
)
