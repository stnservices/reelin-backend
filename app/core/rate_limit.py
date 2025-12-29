"""Rate limiting configuration for API endpoints."""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Check if running in test mode
TESTING = os.environ.get("TESTING", "").lower() in ("1", "true", "yes")

# Create limiter instance using client IP
# In test mode, rate limiting is effectively disabled with high limits
limiter = Limiter(
    key_func=get_remote_address,
    enabled=not TESTING,  # Disable rate limiting in tests
)

# Rate limit configurations
AUTH_RATE_LIMIT = "5/minute"  # 5 requests per minute for auth endpoints
FORGOT_PASSWORD_RATE_LIMIT = "3/minute"  # 3 requests per minute for password reset
