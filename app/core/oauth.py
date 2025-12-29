"""OAuth client configuration for social authentication."""

from authlib.integrations.starlette_client import OAuth

from app.config import get_settings

settings = get_settings()

oauth = OAuth()

# Register Google OAuth provider
if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# Register Facebook OAuth provider
if settings.facebook_client_id and settings.facebook_client_secret:
    oauth.register(
        name="facebook",
        client_id=settings.facebook_client_id,
        client_secret=settings.facebook_client_secret,
        authorize_url="https://www.facebook.com/v18.0/dialog/oauth",
        access_token_url="https://graph.facebook.com/v18.0/oauth/access_token",
        api_base_url="https://graph.facebook.com/v18.0/",
        client_kwargs={"scope": "email public_profile"},
    )
