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
# Note: Increased auth rate limit because tournament events have 50+ users
# connecting from same WiFi network (shared IP) at fishing ponds
AUTH_RATE_LIMIT = "100/minute"  # 100 requests per minute for auth endpoints
FORGOT_PASSWORD_RATE_LIMIT = "3/minute"  # 3 requests per minute for password reset
USER_SEARCH_RATE_LIMIT = "10/minute"  # 10 requests per minute for user search (Story 14.2)
