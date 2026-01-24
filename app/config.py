"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://reelin:reelin_dev@localhost:5432/reelin"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    mobile_access_token_expire_days: int = 7
    mobile_refresh_token_expire_days: int = 30

    # Application
    debug: bool = True
    cors_origins: str = "http://localhost:3000"

    # Storage (S3/DigitalOcean Spaces)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket_name: str = ""
    aws_s3_endpoint_url: str = ""
    aws_s3_region_name: str = ""

    # Email (AWS SES SMTP)
    email_host: str = ""
    email_port: int = 587
    email_use_tls: bool = True
    email_host_user: str = ""
    email_host_password: str = ""
    default_from_email: str = "ReelIn Notifications <noreply@reelin.ro>"
    email_timeout: int = 20
    email_max_retries: int = 3
    email_retry_backoff_seconds: int = 2
    site_name: str = "ReelIn"

    # Email verification
    email_verification_token_expire_hours: int = 24

    # Push Notifications (Firebase Cloud Messaging)
    firebase_credentials: str = ""  # JSON string of service account credentials
    firebase_database_url: str = ""  # Firebase Realtime Database URL (e.g., https://<project-id>-default-rtdb.firebaseio.com)

    # OAuth Providers
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_client_id: str = ""
    facebook_client_secret: str = ""
    apple_bundle_id: str = ""  # iOS app bundle ID for Apple Sign In verification

    # Frontend URL (for OAuth redirects)
    frontend_url: str = "http://localhost:3000"

    # Stripe (for platform billing)
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_invoice_days_until_due: int = 30

    # External APIs
    openweathermap_api_key: str = ""  # For fishing forecast feature

    # reCAPTCHA (Google reCAPTCHA v3)
    recaptcha_secret_key: str = ""
    recaptcha_min_score: float = 0.5  # Minimum score to consider valid (0.0-1.0)
    recaptcha_required: bool = False  # Phase 1: false (optional), Phase 2: true (required)

    # Content Moderation (Profile Picture Safety)
    content_moderation_enabled: bool = True
    google_cloud_credentials_path: str = ""  # Path to service account JSON file
    google_cloud_credentials_json: str = ""  # Or JSON content as string (for Docker secrets)
    # Azure AI Content Safety (fallback - 5,000 free/month)
    azure_content_safety_endpoint: str = ""  # e.g., https://your-resource.cognitiveservices.azure.com
    azure_content_safety_key: str = ""  # API key from Azure portal
    profile_picture_rate_limit_hours: int = 24  # How often user can change profile picture

    # Contact form settings
    contact_admin_email: str = "contact@reelin.ro"

    # App Version (for mobile update checks)
    app_version: str = "1.0.0"  # Current latest version
    app_min_version_ios: str = "1.0.0"  # Minimum supported iOS version
    app_min_version_android: str = "1.0.0"  # Minimum supported Android version
    app_store_url: str = "https://apps.apple.com/app/reelin/id123456789"
    play_store_url: str = "https://play.google.com/store/apps/details?id=ro.reelin.app"

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
