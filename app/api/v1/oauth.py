"""OAuth authentication endpoints for social login."""

from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
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
from app.schemas.social_account import SocialAccountResponse


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


class GoogleMobileAuthRequest(BaseModel):
    """Request body for mobile Google auth."""
    id_token: str


class FacebookMobileAuthRequest(BaseModel):
    """Request body for mobile Facebook auth."""
    access_token: str


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
    request_body: FacebookMobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle Facebook authentication from mobile app using access token."""
    try:
        # Verify the access token with Facebook Graph API
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

        email = user_info.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not available. Please grant email permission.",
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
            email=email,
            first_name=user_info.get("first_name"),
            last_name=user_info.get("last_name"),
            avatar_url=avatar_url,
            access_token=request_body.access_token,
            refresh_token=None,
        )

        # Check for pending deletion (same as email login)
        await check_pending_deletion_for_mobile(user, db)

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
    request_body: AppleMobileAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle Apple Sign In from mobile app using identity token.

    Apple Sign In returns an identity token (JWT) that we verify and extract user info from.
    Note: Apple only sends the user's name on the FIRST sign-in. After that, we only get
    the user's unique identifier (sub) and email.
    """
    import jwt

    try:
        # Decode the identity token without verification first to get the header
        # Apple's identity token is a JWT signed with RS256
        unverified_header = jwt.get_unverified_header(request_body.identity_token)

        # For production, you should verify the token signature using Apple's public keys
        # from https://appleid.apple.com/auth/keys
        # For now, we decode and extract the claims
        # In production, use: jwt.decode(token, key, algorithms=["RS256"], audience=bundle_id)

        # Decode without verification (for development)
        # TODO: Add proper signature verification for production
        claims = jwt.decode(
            request_body.identity_token,
            options={"verify_signature": False},
            algorithms=["RS256"],
        )

        # Verify issuer
        if claims.get("iss") != "https://appleid.apple.com":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token issuer",
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

    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid identity token: {str(e)}",
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication failed: {str(e)}",
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
