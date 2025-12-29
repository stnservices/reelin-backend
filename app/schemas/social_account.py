"""Social account schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.social_account import OAuthProvider


class SocialAccountBase(BaseModel):
    """Base social account schema."""

    provider: OAuthProvider


class SocialAccountResponse(SocialAccountBase):
    """Social account response schema (public fields only)."""

    id: int
    provider_account_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class SocialAccountCreate(SocialAccountBase):
    """Social account creation schema (internal use)."""

    user_id: int
    provider_account_id: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
