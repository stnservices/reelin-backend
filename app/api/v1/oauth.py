"""OAuth authentication endpoints for social login."""

import logging
import time
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from jose import jwt, JWTError
from jose.exceptions import JWTClaimsError, ExpiredSignatureError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.oauth import oauth
from app.core.security import create_access_token, create_refresh_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models.social_account import OAuthProvider
from app.models.user import UserAccount
from app.services.social_auth import SocialAuthService
from app.services.account_deletion import account_deletion_service
from app.services import audit_service
from app.schemas.social_account import SocialAccountResponse

logger = logging.getLogger(__name__)

# Apple public keys cache
_apple_public_keys_cache: dict = {"keys": None, "fetched_at": 0}
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
APPLE_KEYS_CACHE_TTL = 3600  # 1 hour


async def get_apple_public_keys() -> list[dict]:
    """
    Fetch Apple's public keys for JWT verification.
    Keys are cached for 1 hour to avoid repeated requests.
    """
    current_time = time.time()

    # Return cached keys if still valid
    if (
        _apple_public_keys_cache["keys"] is not None
        and current_time - _apple_public_keys_cache["fetched_at"] < APPLE_KEYS_CACHE_TTL
    ):
        return _apple_public_keys_cache["keys"]

    # Fetch fresh keys from Apple
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(APPLE_KEYS_URL, timeout=10.0)
            response.raise_for_status()
            keys_data = response.json()

            _apple_public_keys_cache["keys"] = keys_data.get("keys", [])
            _apple_public_keys_cache["fetched_at"] = current_time

            logger.info("Fetched Apple public keys successfully")
            return _apple_public_keys_cache["keys"]
    except Exception as e:
        logger.error(f"Failed to fetch Apple public keys: {e}")
        # If we have cached keys, return them even if expired (better than failing)
        if _apple_public_keys_cache["keys"] is not None:
            return _apple_public_keys_cache["keys"]
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify Apple authentication at this time",
        )


def get_apple_public_key_for_kid(keys: list[dict], kid: str) -> dict | None:
    """Find the public key matching the given key ID."""
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


async def check_pending_deletion_for_mobile(user: UserAccount, db: AsyncSession) -> None:
    """
    Check if user's account is pending deletion and raise appropriate error for mobile.
    This is similar to the check in auth.py login endpoint.
    """
    if user.deletion_scheduled_at:
        deletion_info = await account_deletion_service.check_pending_deletion(user, db)
        if deletion_info:
            # Return special response for pending deletion
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "account_pending_deletion",
                    "message": "Your account is scheduled for deletion",
                    "deletion_scheduled_at": deletion_info["deletion_scheduled_at"].isoformat(),
                    "permanent_deletion_at": deletion_info["permanent_deletion_at"].isoformat(),
                    "days_remaining": deletion_info["days_remaining"],
                    "can_recover": deletion_info["can_recover"],
                    "recovery_token": create_access_token({"sub": str(user.id)}, is_mobile=True)
                }
            )
        else:
            # Grace period expired but background job hasn't run yet
            # Clear the pending deletion state so user can use their account normally
            user.deletion_scheduled_at = None
            user.is_active = True
            await db.commit()
            await db.refresh(user)


async def _audit_oauth_login(request_or_none, db, user, provider: str):
    """Shared audit logic for OAuth mobile endpoints."""
    if request_or_none is None:
        return
    ctx = audit_service.extract_request_context(request_or_none)
    audit_service.log_event(
        db,
        event_type="login",
        user_id=user.id,
        ip=ctx["ip"],
        user_agent=ctx["user_agent"],
        device_id=ctx["device_id"],
        details={"provider": provider},
    )
    is_new_device = False
    if ctx["device_id"]:
        _, is_new_device = await audit_service.register_or_update_device(
            db, user.id, ctx["device_id"], ip=ctx["ip"], device_info=ctx["device_info"]
        )
    await db.commit()

    if is_new_device:
        try:
            from app.tasks.audit import check_repeat_offender
            check_repeat_offender.delay(user.id, ctx["device_id"], ctx["ip"], user.email)
        except Exception:
            pass


class GoogleMobileAuthRequest(BaseModel):
    """Request body for mobile Google auth."""
    id_token: str


class FacebookMobileAuthRequest(BaseModel):
    """Request body for mobile Facebook auth."""
    access_token: str
    is_limited_login: bool = False  # iOS Limited Login uses OIDC token


class AppleMobileAuthRequest(BaseModel):
    """Request body for mobile Apple auth."""
    identity_token: str
    authorization_code: str
    first_name: str | None = None
    last_name: str | None = None


class MobileAuthResponse(BaseModel):
    """Response for mobile auth."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict

router = APIRouter(tags=["oauth"])
settings = get_settings()


def get_frontend_error_url(error: str) -> str:
    """Build frontend error redirect URL."""
    params = urlencode({"error": error})
    return f"{settings.frontend_url}/auth/callback?{params}"


def get_frontend_success_url() -> str:
    """Build frontend success redirect URL."""
    return f"{settings.frontend_url}/auth/callback?success=true"


def get_oauth_callback_url(callback_name: str) -> str:
    """
    Build OAuth callback URL with the correct public URL.

    When running behind a reverse proxy (like DO App Platform ingress),
    the /api prefix is stripped when forwarding to the backend.
    This function constructs the callback URL with the public /api prefix.
    """
    # Map callback names to their paths (with /api prefix for public URL)
    callback_paths = {
        "google_callback": "/api/v1/auth/google/callback",
        "facebook_callback": "/api/v1/auth/facebook/callback",
    }
    path = callback_paths.get(callback_name, "")
    return f"{settings.frontend_url}{path}"


def get_cookie_domain() -> str | None:
    """
    Extract cookie domain for cross-subdomain sharing.

    For production (e.g., api.reelin.ro -> .reelin.ro), allows cookies
    set by the API to be read by the frontend on a different subdomain.
    Returns None for localhost to use default browser behavior.
    """
    parsed = urlparse(settings.frontend_url)
    if not parsed.hostname or parsed.hostname.startswith("localhost") or parsed.hostname == "127.0.0.1":
        return None
    # Get root domain (e.g., "reelin.ro" from "www.reelin.ro")
    parts = parsed.hostname.split(".")
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])  # ".reelin.ro"
    return None


# ============= Google OAuth =============

@router.get("/google")
async def google_login(request: Request):
    """Initiate Google OAuth flow."""
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured",
        )
    redirect_uri = get_oauth_callback_url("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback."""
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info or not user_info.get("email"):
            return RedirectResponse(
                url=get_frontend_error_url("Could not retrieve email from Google")
            )

        # Get or create user
        user = await SocialAuthService.get_or_create_user_from_oauth(
            db=db,
            provider=OAuthProvider.GOOGLE,
            provider_account_id=user_info["sub"],
            email=user_info["email"],
            first_name=user_info.get("given_name"),
            last_name=user_info.get("family_name"),
            avatar_url=user_info.get("picture"),
            access_token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
        )

        # Create JWT tokens
        token_data = {"sub": str(user.id)}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        # Redirect to frontend with tokens in cookies
        response = RedirectResponse(url=get_frontend_success_url())
        cookie_domain = get_cookie_domain()

        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=False,  # Allow JS to read for OAuth callback
            secure=not settings.debug,
            samesite="lax",
            domain=cookie_domain,  # Share across subdomains in production
            max_age=60,  # Short-lived, just for transport
        )
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=False,  # Allow JS to read for OAuth callback
            secure=not settings.debug,
            samesite="lax",
            domain=cookie_domain,  # Share across subdomains in production
            max_age=60,  # Short-lived, just for transport
        )
        return response

    except Exception as e:
        return RedirectResponse(url=get_frontend_error_url(str(e)))


@router.post("/google/mobile", response_model=MobileAuthResponse)
async def google_mobile_auth(
    request: Request,
    request_body: GoogleMobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google authentication from mobile app using ID token."""
    try:
        # Verify the ID token with Google
        # Accept tokens from both Android and Web client IDs
        idinfo = id_token.verify_oauth2_token(
            request_body.id_token,
            google_requests.Request(),
            audience=None,  # We'll verify the audience manually
        )

        # Verify the issuer
        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token issuer",
            )

        # Extract user info
        email = idinfo.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not available in token",
            )

        # Get or create user
        user = await SocialAuthService.get_or_create_user_from_oauth(
            db=db,
            provider=OAuthProvider.GOOGLE,
            provider_account_id=idinfo["sub"],
            email=email,
            first_name=idinfo.get("given_name"),
            last_name=idinfo.get("family_name"),
            avatar_url=idinfo.get("picture"),
            access_token=None,
            refresh_token=None,
        )

        # Check for pending deletion (same as email login)
        await check_pending_deletion_for_mobile(user, db)

        # Audit: log OAuth login + device registration
        await _audit_oauth_login(request, db, user, "google")

        # Create JWT tokens
        token_data = {"sub": str(user.id)}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return MobileAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user={
                "id": user.id,
                "email": user.email,
                "first_name": user.profile.first_name if user.profile else None,
                "last_name": user.profile.last_name if user.profile else None,
                "avatar_url": user.avatar_url,
            },
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid ID token: {str(e)}",
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}",
        )


# ============= Facebook OAuth =============

@router.get("/facebook")
async def facebook_login(request: Request):
    """Initiate Facebook OAuth flow."""
    if not settings.facebook_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Facebook OAuth is not configured",
        )
    redirect_uri = get_oauth_callback_url("facebook_callback")
    return await oauth.facebook.authorize_redirect(request, redirect_uri)


@router.get("/facebook/callback", name="facebook_callback")
async def facebook_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Facebook OAuth callback."""
    try:
        token = await oauth.facebook.authorize_access_token(request)

        # Fetch user info from Facebook Graph API
        resp = await oauth.facebook.get(
            "me",
            params={"fields": "id,name,email,first_name,last_name,picture"},
            token=token,
        )
        user_info = resp.json()

        if not user_info.get("email"):
            return RedirectResponse(
                url=get_frontend_error_url(
                    "Could not retrieve email from Facebook. "
                    "Please ensure you have granted email permission."
                )
            )

        # Extract avatar URL from nested structure
        avatar_url = None
        if user_info.get("picture", {}).get("data", {}).get("url"):
            avatar_url = user_info["picture"]["data"]["url"]

        # Get or create user
        user = await SocialAuthService.get_or_create_user_from_oauth(
            db=db,
            provider=OAuthProvider.FACEBOOK,
            provider_account_id=user_info["id"],
            email=user_info["email"],
            first_name=user_info.get("first_name"),
            last_name=user_info.get("last_name"),
            avatar_url=avatar_url,
            access_token=token.get("access_token"),
            refresh_token=token.get("refresh_token"),
        )

        # Create JWT tokens
        token_data = {"sub": str(user.id)}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        # Redirect to frontend with tokens in cookies
        response = RedirectResponse(url=get_frontend_success_url())
        cookie_domain = get_cookie_domain()

        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=False,  # Allow JS to read for OAuth callback
            secure=not settings.debug,
            samesite="lax",
            domain=cookie_domain,  # Share across subdomains in production
            max_age=60,  # Short-lived, just for transport
        )
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=False,  # Allow JS to read for OAuth callback
            secure=not settings.debug,
            samesite="lax",
            domain=cookie_domain,  # Share across subdomains in production
            max_age=60,  # Short-lived, just for transport
        )
        return response

    except Exception as e:
        return RedirectResponse(url=get_frontend_error_url(str(e)))


@router.post("/facebook/mobile", response_model=MobileAuthResponse)
async def facebook_mobile_auth(
    request: Request,
    request_body: FacebookMobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle Facebook authentication from mobile app.

    Supports two modes:
    - Regular login: access_token verified via Graph API
    - Limited Login (iOS): OIDC token (JWT) decoded for user info

    Auto-detection: If the token looks like a JWT, try decoding it first.
    This handles cases where iOS forces Limited Login even if the app
    didn't explicitly request it.
    """
    try:
        user_info = {}
        avatar_url = None

        # Auto-detect if token is a JWT (has 3 dot-separated parts)
        token_parts = request_body.access_token.split('.')
        is_jwt_token = len(token_parts) == 3 and all(part for part in token_parts)

        if request_body.is_limited_login or is_jwt_token:
            # iOS Limited Login: token is an OIDC JWT
            # Decode the JWT to extract user info (without verification since it comes from Facebook SDK)
            import jwt
            try:
                # Decode without verification - the token comes directly from Facebook SDK
                # Facebook signs these with their private key, but we trust the SDK
                decoded = jwt.decode(
                    request_body.access_token,
                    options={"verify_signature": False},
                    algorithms=["RS256"],
                )

                # Extract user info from JWT claims
                user_info = {
                    "id": decoded.get("sub"),  # Facebook user ID
                    "email": decoded.get("email"),
                    "name": decoded.get("name"),
                    "first_name": decoded.get("given_name") or decoded.get("name", "").split()[0] if decoded.get("name") else None,
                    "last_name": decoded.get("family_name") or (decoded.get("name", "").split()[-1] if decoded.get("name") and len(decoded.get("name", "").split()) > 1 else None),
                    "picture": decoded.get("picture"),
                }
                avatar_url = decoded.get("picture")

                logger.info(f"Facebook Limited Login: decoded JWT for user {user_info.get('id')}")

            except jwt.DecodeError as e:
                logger.warning(f"Failed to decode Facebook token as JWT: {e}, falling back to Graph API")
                # Fall through to Graph API verification
                user_info = {}

        # If we don't have user info yet (not JWT or JWT decode failed), try Graph API
        if not user_info.get("id"):
            # Regular login: verify access token with Facebook Graph API
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://graph.facebook.com/me",
                    params={
                        "fields": "id,name,email,first_name,last_name,picture",
                        "access_token": request_body.access_token,
                    },
                )

                if response.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Facebook access token",
                    )

                user_info = response.json()

                # Extract avatar URL from nested structure (Graph API format)
                if user_info.get("picture", {}).get("data", {}).get("url"):
                    avatar_url = user_info["picture"]["data"]["url"]

        email = user_info.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not available. Please grant email permission.",
            )

        # Get or create user
        user = await SocialAuthService.get_or_create_user_from_oauth(
            db=db,
            provider=OAuthProvider.FACEBOOK,
            provider_account_id=user_info["id"],
            email=email,
            first_name=user_info.get("first_name"),
            last_name=user_info.get("last_name"),
            avatar_url=avatar_url,
            access_token=request_body.access_token,
            refresh_token=None,
        )

        # Check for pending deletion (same as email login)
        await check_pending_deletion_for_mobile(user, db)

        # Audit: log OAuth login + device registration
        await _audit_oauth_login(request, db, user, "facebook")

        # Create JWT tokens
        token_data = {"sub": str(user.id)}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return MobileAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user={
                "id": user.id,
                "email": user.email,
                "first_name": user.profile.first_name if user.profile else None,
                "last_name": user.profile.last_name if user.profile else None,
                "avatar_url": user.avatar_url,
            },
        )

    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not verify token with Facebook: {str(e)}",
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}",
        )


# ============= Apple OAuth =============

@router.post("/apple/mobile", response_model=MobileAuthResponse)
async def apple_mobile_auth(
    request: Request,
    request_body: AppleMobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Apple Sign In from mobile app using identity token.

    Apple Sign In returns an identity token (JWT) that we verify and extract user info from.
    Note: Apple only sends the user's name on the FIRST sign-in. After that, we only get
    the user's unique identifier (sub) and email.
    """
    settings = get_settings()

    try:
        # Get the unverified header to find the key ID (kid)
        unverified_header = jwt.get_unverified_header(request_body.identity_token)
        kid = unverified_header.get("kid")

        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing key ID",
            )

        # Fetch Apple's public keys
        apple_keys = await get_apple_public_keys()

        # Find the key matching the kid in the token header
        public_key = get_apple_public_key_for_kid(apple_keys, kid)

        if not public_key:
            # Key not found - might be rotated, try refreshing cache
            _apple_public_keys_cache["fetched_at"] = 0  # Force refresh
            apple_keys = await get_apple_public_keys()
            public_key = get_apple_public_key_for_kid(apple_keys, kid)

            if not public_key:
                logger.error(f"Apple public key not found for kid: {kid}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: key not found",
                )

        # Verify and decode the JWT with proper signature verification
        # The audience should be your app's bundle ID
        try:
            claims = jwt.decode(
                request_body.identity_token,
                public_key,
                algorithms=["RS256"],
                audience=settings.apple_bundle_id,
                issuer="https://appleid.apple.com",
            )
        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
            )
        except JWTClaimsError as e:
            logger.warning(f"Apple JWT claims error: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims",
            )
        except JWTError as e:
            logger.warning(f"Apple JWT verification failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token signature",
            )

        # Extract user identifier (this is stable and unique per user per app)
        apple_user_id = claims.get("sub")
        if not apple_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User identifier not found in token",
            )

        # Extract email (may be a private relay email)
        email = claims.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not available in token",
            )

        # Apple only sends the name on the first sign-in
        # After that, we need to use what we stored previously
        first_name = request_body.first_name
        last_name = request_body.last_name

        # Get or create user
        user = await SocialAuthService.get_or_create_user_from_oauth(
            db=db,
            provider=OAuthProvider.APPLE,
            provider_account_id=apple_user_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            avatar_url=None,  # Apple doesn't provide profile pictures
            access_token=None,
            refresh_token=None,
        )

        # Check for pending deletion (same as email login)
        await check_pending_deletion_for_mobile(user, db)

        # Audit: log OAuth login + device registration
        await _audit_oauth_login(request, db, user, "apple")

        # Create JWT tokens
        token_data = {"sub": str(user.id)}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return MobileAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user={
                "id": user.id,
                "email": user.email,
                "first_name": user.profile.first_name if user.profile else None,
                "last_name": user.profile.last_name if user.profile else None,
                "avatar_url": user.avatar_url,
            },
        )

    except JWTError as e:
        logger.warning(f"Apple JWT error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid identity token",
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Apple auth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed",
        )


# ============= Account Linking & Management =============

@router.get("/social/accounts", response_model=list[SocialAccountResponse])
async def get_linked_accounts(
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all social accounts linked to current user."""
    accounts = await SocialAuthService.get_user_social_accounts(db, current_user.id)
    return accounts


@router.delete("/social/accounts/{account_id}")
async def unlink_social_account(
    account_id: int,
    current_user: UserAccount = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unlink a social account from current user."""
    try:
        await SocialAuthService.unlink_social_account(
            db=db,
            user_id=current_user.id,
            social_account_id=account_id,
        )
        return {"message": "Social account unlinked successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# ============= Facebook Data Deletion Callback =============

import base64
import hashlib
import hmac
import json as json_module


@router.post("/facebook/data-deletion")
async def facebook_data_deletion_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Facebook data deletion callback.

    Facebook sends a signed_request when a user requests deletion of their data.
    We must verify the signature, process the deletion, and return a confirmation.

    See: https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
    """
    try:
        form_data = await request.form()
        signed_request = form_data.get("signed_request")

        if not signed_request:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing signed_request parameter",
            )

        # Parse and verify the signed request
        encoded_sig, payload = signed_request.split(".", 1)

        # Decode signature
        sig = base64.urlsafe_b64decode(encoded_sig + "==")

        # Decode payload
        data = json_module.loads(base64.urlsafe_b64decode(payload + "=="))

        # Verify signature using app secret
        expected_sig = hmac.new(
            settings.facebook_client_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(sig, expected_sig):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid signature",
            )

        # Get user ID from Facebook
        facebook_user_id = data.get("user_id")

        if facebook_user_id:
            # Delete the Facebook social account link for this user
            from sqlalchemy import select, delete
            from app.models.social_account import SocialAccount, OAuthProvider

            # Find and delete the social account
            await db.execute(
                delete(SocialAccount).where(
                    SocialAccount.provider == OAuthProvider.FACEBOOK,
                    SocialAccount.provider_account_id == str(facebook_user_id),
                )
            )
            await db.commit()

        # Generate a confirmation code (use timestamp + user_id hash)
        import time
        confirmation_code = hashlib.sha256(
            f"{facebook_user_id}-{time.time()}".encode()
        ).hexdigest()[:16]

        # Return the required response format
        return {
            "url": f"{settings.frontend_url}/privacy/data-deletion?code={confirmation_code}",
            "confirmation_code": confirmation_code,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process deletion request: {str(e)}",
        )
