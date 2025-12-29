"""Security utilities: password hashing, JWT token creation/validation."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    is_mobile: bool = False,
) -> str:
    """
    Create a JWT access token.

    Args:
        data: Data to encode in the token (should include 'sub' for user_id)
        expires_delta: Optional custom expiration time
        is_mobile: If True, use longer expiration for mobile apps

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    elif is_mobile:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.mobile_access_token_expire_days)
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),  # Unique token ID for blacklisting
        "type": "access",
    })

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    is_mobile: bool = False,
) -> str:
    """
    Create a JWT refresh token.

    Args:
        data: Data to encode in the token (should include 'sub' for user_id)
        expires_delta: Optional custom expiration time
        is_mobile: If True, use longer expiration for mobile apps

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    elif is_mobile:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.mobile_refresh_token_expire_days)
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
        "type": "refresh",
    })

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        JWTError: If token is invalid or expired
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def create_password_reset_token(user_id: int) -> str:
    """
    Create a password reset token.

    Args:
        user_id: User ID to encode in the token

    Returns:
        Encoded JWT token string (valid for 1 hour)
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    to_encode = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
        "type": "password_reset",
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_email_verification_token(user_id: int) -> str:
    """
    Create an email verification token.

    Args:
        user_id: User ID to encode in the token

    Returns:
        Encoded JWT token string (valid for 24 hours)
    """
    expire = datetime.now(timezone.utc) + timedelta(
        hours=settings.email_verification_token_expire_hours
    )
    to_encode = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
        "type": "email_verification",
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def get_token_jti(token: str) -> Optional[str]:
    """
    Extract the JTI (JWT ID) from a token without full validation.

    Args:
        token: JWT token string

    Returns:
        JTI string or None if extraction fails
    """
    try:
        # Decode without verification to get JTI
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
        return payload.get("jti")
    except JWTError:
        return None
