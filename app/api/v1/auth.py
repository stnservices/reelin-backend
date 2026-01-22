"""Authentication endpoints."""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Check if running in test mode
TESTING = os.environ.get("TESTING", "").lower() in ("1", "true", "yes")
from jose import JWTError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.core.rate_limit import limiter, AUTH_RATE_LIMIT, FORGOT_PASSWORD_RATE_LIMIT
from app.core.security import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.dependencies import get_current_user
from app.models.user import UserAccount, UserProfile, TokenBlacklist
from app.models.follow import UserFollow
from app.schemas.user import (
    ActivationRequest,
    PasswordChange,
    PasswordReset,
    PasswordResetConfirm,
    ResendVerificationRequest,
    UserCreate,
    UserLogin,
    UserResponse,
    UserProfileResponse,
    TokenResponse,
    TokenRefresh,
)
from app.schemas.common import MessageResponse
from app.services.email import get_email_service
from app.services.account_deletion import account_deletion_service
from app.api.v1.public import verify_recaptcha

router = APIRouter()
logger = __import__("logging").getLogger(__name__)
settings = get_settings()


class AccountPendingDeletionResponse(Exception):
    """Custom exception for accounts pending deletion."""
    def __init__(self, deletion_info: dict, user_id: int):
        self.deletion_info = deletion_info
        self.user_id = user_id


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserAccount:
    """
    Register a new user account.
    """
    # Validate reCAPTCHA if required or if token provided
    if settings.recaptcha_required or user_data.recaptcha_token:
        if not user_data.recaptcha_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_MISSING", "message": "reCAPTCHA token required"},
            )
        is_valid, score = await verify_recaptcha(user_data.recaptcha_token)
        if not is_valid:
            logger.warning(f"reCAPTCHA failed: action=register, email={user_data.email}, score={score}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_FAILED", "message": "Security verification failed", "score": score},
            )
        logger.info(f"reCAPTCHA passed: action=register, score={score}")

    # Check if email already exists
    existing_query = select(UserAccount).where(UserAccount.email == user_data.email)
    result = await db.execute(existing_query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user account
    # In test mode, auto-verify users to skip email verification flow
    user = UserAccount(
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
        is_active=True,
        is_verified=TESTING,  # Auto-verify in test mode, otherwise requires email verification
    )
    db.add(user)
    await db.flush()  # Get the user ID

    # Create user profile with default "angler" role
    profile = UserProfile(
        user_id=user.id,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        roles=["angler"],  # Default role
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)

    # Load profile relationship
    await db.refresh(user, ["profile"])

    # Send verification email
    email_service = get_email_service()
    if email_service.is_configured():
        verification_token = create_email_verification_token(user.id)
        email_service.send_activation_email(
            to_email=user.email,
            first_name=user_data.first_name,
            activation_token=verification_token,
        )

    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    request: Request,
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Authenticate user and return JWT tokens.
    """
    # Validate reCAPTCHA if required or if token provided
    if settings.recaptcha_required or credentials.recaptcha_token:
        if not credentials.recaptcha_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_MISSING", "message": "reCAPTCHA token required"},
            )
        is_valid, score = await verify_recaptcha(credentials.recaptcha_token)
        if not is_valid:
            logger.warning(f"reCAPTCHA failed: action=login, email={credentials.email}, score={score}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_FAILED", "message": "Security verification failed", "score": score},
            )
        logger.info(f"reCAPTCHA passed: action=login, score={score}")

    # Find user by email
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.email == credentials.email)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Check if account is pending deletion BEFORE checking is_active
    # (because we set is_active=False when scheduling deletion, but users should still be able to recover)
    if user.deletion_scheduled_at:
        deletion_info = await account_deletion_service.check_pending_deletion(user, db)
        if deletion_info:
            # Return special response for pending deletion
            # This allows the mobile app to show a recovery dialog
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "account_pending_deletion",
                    "message": "Your account is scheduled for deletion",
                    "deletion_scheduled_at": deletion_info["deletion_scheduled_at"].isoformat(),
                    "permanent_deletion_at": deletion_info["permanent_deletion_at"].isoformat(),
                    "days_remaining": deletion_info["days_remaining"],
                    "can_recover": deletion_info["can_recover"],
                    # Include a recovery token so user can recover without full re-auth
                    "recovery_token": create_access_token({"sub": str(user.id)}, is_mobile=credentials.is_mobile)
                }
            )
        else:
            # Grace period expired but background job hasn't run yet
            # Clear the pending deletion state so user can use their account normally
            user.deletion_scheduled_at = None
            user.is_active = True
            await db.commit()
            await db.refresh(user)

    # Check is_active (for accounts that are deactivated but NOT pending deletion)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please verify your email address before logging in",
        )

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    # Create tokens - sub must be a string for JWT
    token_data = {"sub": str(user.id)}
    access_token = create_access_token(token_data, is_mobile=credentials.is_mobile)
    refresh_token = create_refresh_token(token_data, is_mobile=credentials.is_mobile)

    # Calculate expiration
    if credentials.is_mobile:
        expires_in = settings.mobile_access_token_expire_days * 24 * 60 * 60
    else:
        expires_in = settings.access_token_expire_minutes * 60

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    token_data: TokenRefresh,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Refresh access token using refresh token.
    """
    try:
        payload = decode_token(token_data.refresh_token)
        user_id_str: str = payload.get("sub")
        token_type: str = payload.get("type")
        token_jti: str = payload.get("jti")

        if token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )

        # Convert string user_id to int
        user_id = int(user_id_str)

    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Check if token is blacklisted
    blacklist_query = select(TokenBlacklist).where(TokenBlacklist.token_jti == token_jti)
    blacklist_result = await db.execute(blacklist_query)
    if blacklist_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    # Verify user exists
    user_query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Allow refresh for pending deletion accounts (so they can recover)
    # But reject permanently deactivated accounts
    if not user.is_active and not user.deletion_scheduled_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )

    # Blacklist old refresh token (token rotation)
    old_token_blacklist = TokenBlacklist(
        token_jti=token_jti,
        user_id=user_id,
        token_type="refresh",
        expires_at=datetime.fromtimestamp(payload.get("exp"), tz=timezone.utc),
    )
    db.add(old_token_blacklist)

    # Create new tokens - sub must be a string
    token_payload = {"sub": str(user_id)}
    new_access_token = create_access_token(token_payload)
    new_refresh_token = create_refresh_token(token_payload)

    try:
        await db.commit()
    except IntegrityError:
        # Token already blacklisted (race condition or duplicate request)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has already been used",
        )

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Logout user by blacklisting current access token.
    Note: Client should also discard the refresh token.
    """
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )

    token = auth_header.split(" ")[1]

    try:
        # Decode token to get JTI and expiration
        payload = decode_token(token)
        token_jti = payload.get("jti")
        token_exp = payload.get("exp")

        if not token_jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token format",
            )

        # Add token to blacklist
        blacklist_entry = TokenBlacklist(
            token_jti=token_jti,
            user_id=current_user.id,
            token_type="access",
            expires_at=datetime.fromtimestamp(token_exp, tz=timezone.utc),
        )
        db.add(blacklist_entry)
        await db.commit()

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current authenticated user information.
    """
    # Get follower/following counts
    follower_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.following_id == current_user.id)
    )
    follower_count = follower_result.scalar() or 0

    following_result = await db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == current_user.id)
    )
    following_count = following_result.scalar() or 0

    # Build profile response with follower counts
    profile_response = None
    if current_user.profile:
        profile = current_user.profile
        profile_response = UserProfileResponse(
            id=profile.id,
            first_name=profile.first_name,
            last_name=profile.last_name,
            phone=profile.phone,
            bio=profile.bio,
            gender=profile.gender,
            profile_picture_url=profile.profile_picture_url,
            roles=profile.roles or [],
            country_id=profile.country_id,
            city_id=profile.city_id,
            country_name=profile.country.name if profile.country else None,
            city_name=profile.city.name if profile.city else None,
            facebook_url=profile.facebook_url,
            instagram_url=profile.instagram_url,
            tiktok_url=profile.tiktok_url,
            youtube_url=profile.youtube_url,
            is_profile_public=profile.is_profile_public,
            follower_count=follower_count,
            following_count=following_count,
            created_at=profile.created_at,
        )

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        is_pro=current_user.is_pro,
        has_password=current_user.has_password,  # Important for social login users
        created_at=current_user.created_at,
        last_login=current_user.last_login,
        profile=profile_response,
    )


@router.post("/activate", response_model=MessageResponse)
async def activate_account(
    data: ActivationRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Activate a user account using the verification token sent via email.

    The token is single-use and expires after 24 hours.
    """
    try:
        # Decode and validate the verification token
        payload = decode_token(data.token)
        token_type = payload.get("type")
        token_jti = payload.get("jti")
        user_id_str = payload.get("sub")

        if token_type != "email_verification":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token type",
            )

        user_id = int(user_id_str)

    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    # Check if token was already used (blacklisted)
    blacklist_query = select(TokenBlacklist).where(TokenBlacklist.token_jti == token_jti)
    blacklist_result = await db.execute(blacklist_query)
    if blacklist_result.scalar_one_or_none():
        # Token was used - but account should be activated, return success with flag
        return {"message": "Account is already activated", "already_activated": True}

    # Find the user
    user_query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification token",
        )

    if user.is_verified:
        return {"message": "Account is already activated", "already_activated": True}

    # Activate the user
    user.is_verified = True

    # Blacklist the verification token (single use)
    blacklist_entry = TokenBlacklist(
        token_jti=token_jti,
        user_id=user_id,
        token_type="email_verification",
        expires_at=datetime.fromtimestamp(payload.get("exp"), tz=timezone.utc),
    )
    db.add(blacklist_entry)

    await db.commit()

    return {"message": "Account activated successfully"}


# Rate limit for resend verification: 1 per minute per IP
RESEND_VERIFICATION_RATE_LIMIT = "1/minute"


@router.post("/resend-verification", response_model=MessageResponse)
@limiter.limit(RESEND_VERIFICATION_RATE_LIMIT)
async def resend_verification(
    request: Request,
    data: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Resend the account verification email.

    Rate limited to 1 request per minute per IP address.
    Returns same message regardless of email existence to prevent enumeration.
    """
    # Find user by email
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.email == data.email)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user and user.is_active and not user.is_verified:
        # Send verification email
        email_service = get_email_service()
        if email_service.is_configured():
            verification_token = create_email_verification_token(user.id)
            first_name = user.profile.first_name if user.profile else "User"
            email_service.send_activation_email(
                to_email=user.email,
                first_name=first_name,
                activation_token=verification_token,
            )

    # Return same message regardless of email existence (prevents enumeration)
    return {"message": "If an account with that email exists and is not yet verified, a verification email has been sent."}


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit(FORGOT_PASSWORD_RATE_LIMIT)
async def forgot_password(
    request: Request,
    data: PasswordReset,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Request a password reset email.

    Sends a password reset email with a secure link.
    Note: Always returns success to prevent email enumeration attacks.
    """
    # Validate reCAPTCHA if required or if token provided
    if settings.recaptcha_required or data.recaptcha_token:
        if not data.recaptcha_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_MISSING", "message": "reCAPTCHA token required"},
            )
        is_valid, score = await verify_recaptcha(data.recaptcha_token)
        if not is_valid:
            logger.warning(f"reCAPTCHA failed: action=forgot_password, email={data.email}, score={score}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "RECAPTCHA_FAILED", "message": "Security verification failed", "score": score},
            )
        logger.info(f"reCAPTCHA passed: action=forgot_password, score={score}")

    # Find user by email
    query = (
        select(UserAccount)
        .options(selectinload(UserAccount.profile))
        .where(UserAccount.email == data.email)
    )
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    details = None
    if user and user.is_active:
        # Generate password reset token
        reset_token = create_password_reset_token(user.id)

        # Send password reset email
        email_service = get_email_service()
        if email_service.is_configured():
            first_name = user.profile.first_name if user.profile else "User"
            email_service.send_password_reset_email(
                to_email=user.email,
                first_name=first_name,
                reset_token=reset_token,
            )

        # In test mode, return the token for testing purposes
        if TESTING:
            details = {"reset_token": reset_token}

    # Return same message even if user not found (prevents enumeration)
    return {
        "message": "If an account with that email exists, a password reset link has been sent.",
        "details": details,
    }


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    data: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reset password using a valid reset token.

    Validates the token and updates the user's password.
    The token can only be used once.
    """
    try:
        # Decode and validate the reset token
        payload = decode_token(data.token)
        token_type = payload.get("type")
        token_jti = payload.get("jti")
        user_id_str = payload.get("sub")

        if token_type != "password_reset":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token type",
            )

        user_id = int(user_id_str)

    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    # Check if token was already used (blacklisted)
    blacklist_query = select(TokenBlacklist).where(TokenBlacklist.token_jti == token_jti)
    blacklist_result = await db.execute(blacklist_query)
    if blacklist_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token has already been used",
        )

    # Find the user
    user_query = select(UserAccount).where(UserAccount.id == user_id)
    result = await db.execute(user_query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is deactivated",
        )

    # Update password
    user.password_hash = get_password_hash(data.new_password)

    # Blacklist the reset token (single use)
    blacklist_entry = TokenBlacklist(
        token_jti=token_jti,
        user_id=user_id,
        token_type="password_reset",
        expires_at=datetime.fromtimestamp(payload.get("exp"), tz=timezone.utc),
    )
    db.add(blacklist_entry)

    await db.commit()

    return {"message": "Password has been reset successfully"}


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    data: PasswordChange,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Change password for the authenticated user.

    Requires current password verification before allowing change.
    """
    # Verify current password
    if not current_user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for social login accounts. Please use 'Forgot Password' to set a password.",
        )

    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Ensure new password is different from current
    if verify_password(data.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password",
        )

    # Update password
    current_user.password_hash = get_password_hash(data.new_password)
    await db.commit()

    return {"message": "Password changed successfully"}


@router.get("/firebase-token")
async def get_firebase_token(
    current_user: UserAccount = Depends(get_current_user),
) -> dict:
    """
    Get a Firebase custom token for the authenticated user.

    This token is used to authenticate with Firebase services
    (Realtime Database, Firestore) from the mobile app.
    The token is valid for 1 hour.

    Returns:
        firebase_token: Custom token for Firebase authentication
    """
    from app.services.push_notifications import create_firebase_custom_token

    token = create_firebase_custom_token(current_user.id)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase service unavailable",
        )

    return {"firebase_token": token}

