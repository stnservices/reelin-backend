"""User-related Pydantic schemas."""

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ============== Email Domain Validation ==============

# Common TLD typos -> correct TLD (only obvious typos)
TLD_TYPOS = {
    # .com typos (keyboard proximity & double letters)
    "comm": "com",
    "cmo": "com",
    "ocm": "com",
    "coom": "com",
    "comi": "com",
    "comn": "com",
    "coml": "com",
    "cpm": "com",  # p next to o
    "vom": "com",  # v next to c
    "xom": "com",  # x next to c
    "con": "com",  # n next to m
    "clm": "com",  # l next to o
    "dom": "com",  # d next to c (rare)
    "comp": "com",  # extra p
    "coim": "com",  # i next to o
    # .net typos
    "nett": "net",
    "nte": "net",
    "nrt": "net",  # r next to e
    "met": "net",  # m next to n
    "bet": "net",  # b next to n
    "neт": "net",  # cyrillic т
    "ner": "net",  # r next to t
    # .org typos
    "orgg": "org",
    "ogr": "org",
    "orf": "org",  # f next to g
    "otg": "org",  # t next to r
    "prg": "org",  # p next to o
    "irg": "org",  # i next to o
    # .ro typos (Romanian)
    "roo": "ro",
    "r0": "ro",  # zero instead of o
    "ro0": "ro",
    "ri": "ro",  # i next to o
    "rp": "ro",  # p next to o
    # .uk typos
    "ukk": "uk",
    "yk": "uk",  # y next to u
    # .de typos (German)
    "dee": "de",
    "fe": "de",  # f next to d
    # .fr typos (French)
    "frr": "fr",
    "ft": "fr",  # t next to r
    # .it typos (Italian)
    "itt": "it",
    "ir": "it",  # r next to t
    # .pl typos (Polish)
    "pll": "pl",
    "ol": "pl",  # o next to p
}

# Common email provider domain typos -> correct domain
DOMAIN_TYPOS = {
    # Gmail (most common)
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmali.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmeil.com": "gmail.com",
    "gimail.com": "gmail.com",
    "gemail.com": "gmail.com",
    "gmaik.com": "gmail.com",  # k next to l
    "gmaio.com": "gmail.com",  # o next to l
    "gmailc.om": "gmail.com",
    "gmail.cом": "gmail.com",  # cyrillic о
    "gmsil.com": "gmail.com",  # s next to a
    "gmaul.com": "gmail.com",  # u next to i
    "hmail.com": "gmail.com",  # h next to g
    "fmail.com": "gmail.com",  # f next to g
    "vmail.com": "gmail.com",  # v next to g (rare but happens)
    "g]mail.com": "gmail.com",  # accidental bracket
    "g,ail.com": "gmail.com",  # comma instead of m
    "gmaol.com": "gmail.com",  # o instead of i
    # Yahoo
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "yhaoo.com": "yahoo.com",
    "yhoo.com": "yahoo.com",
    "yahho.com": "yahoo.com",
    "yaoo.com": "yahoo.com",
    "uahoo.com": "yahoo.com",  # u next to y
    "tahoo.com": "yahoo.com",  # t next to y
    "yaboo.com": "yahoo.com",  # b next to h
    "yshoo.com": "yahoo.com",  # s next to a
    "yahii.com": "yahoo.com",  # ii instead of oo
    "yahoo.cом": "yahoo.com",  # cyrillic
    # Hotmail
    "hotmai.com": "hotmail.com",
    "hotmal.com": "hotmail.com",
    "hotmial.com": "hotmail.com",
    "hotmil.com": "hotmail.com",
    "hotmaill.com": "hotmail.com",
    "hotamil.com": "hotmail.com",
    "hotmailc.om": "hotmail.com",
    "hitmail.com": "hotmail.com",  # i next to o
    "hormail.com": "hotmail.com",  # r next to t
    "hotmsil.com": "hotmail.com",  # s next to a
    "hotmqil.com": "hotmail.com",  # q next to a
    "jotmail.com": "hotmail.com",  # j next to h
    "gotmail.com": "hotmail.com",  # g next to h
    # Outlook
    "outlok.com": "outlook.com",
    "outloo.com": "outlook.com",
    "outlool.com": "outlook.com",
    "outloook.com": "outlook.com",
    "outlokk.com": "outlook.com",
    "outllok.com": "outlook.com",
    "outlouk.com": "outlook.com",
    "putlook.com": "outlook.com",  # p next to o
    "iutlook.com": "outlook.com",  # i next to o
    "oitlook.com": "outlook.com",  # i next to u
    "ourlook.com": "outlook.com",  # r next to t
    # Live.com (Microsoft)
    "liv.com": "live.com",
    "livee.com": "live.com",
    "lve.com": "live.com",
    "kive.com": "live.com",  # k next to l
    "lige.com": "live.com",  # g next to v
    # iCloud
    "icoud.com": "icloud.com",
    "iclod.com": "icloud.com",
    "iclould.com": "icloud.com",
    "icluod.com": "icloud.com",
    "icloid.com": "icloud.com",
    "iclud.com": "icloud.com",
    "iclooud.com": "icloud.com",
    "ixloud.com": "icloud.com",  # x next to c
    "ivloud.com": "icloud.com",  # v next to c
    "ucloud.com": "icloud.com",  # u next to i
    # Protonmail
    "protonmal.com": "protonmail.com",
    "protonmial.com": "protonmail.com",
    "protonmil.com": "protonmail.com",
    "protonmaill.com": "protonmail.com",
    "protonmai.com": "protonmail.com",
    "protonmqil.com": "protonmail.com",
    "protonmsil.com": "protonmail.com",
    "pritonmail.com": "protonmail.com",  # i next to o
    "ptotonmail.com": "protonmail.com",  # missing r
    # AOL
    "aoll.com": "aol.com",
    "aol.cpm": "aol.com",
    "ail.com": "aol.com",  # wrong vowel
    "sol.com": "aol.com",  # s next to a
    "qol.com": "aol.com",  # q next to a
}


def validate_email_domain(email: str) -> str:
    """
    Validate email domain for common typos only.
    Does NOT reject unknown domains - only catches obvious mistakes.
    """
    if not email or "@" not in email:
        return email

    local_part, domain = email.rsplit("@", 1)
    domain_lower = domain.lower()

    # Check for known provider domain typos (gmail, yahoo, etc.)
    if domain_lower in DOMAIN_TYPOS:
        correct_domain = DOMAIN_TYPOS[domain_lower]
        raise ValueError(
            f"Did you mean '{local_part}@{correct_domain}'? "
            f"'{domain}' appears to be a typo."
        )

    # Check for obvious TLD typos only
    if "." in domain_lower:
        tld = domain_lower.rsplit(".", 1)[-1]

        if tld in TLD_TYPOS:
            correct_tld = TLD_TYPOS[tld]
            correct_domain = domain_lower.rsplit(".", 1)[0] + "." + correct_tld
            raise ValueError(
                f"Did you mean '{local_part}@{correct_domain}'? "
                f"'.{tld}' appears to be a typo for '.{correct_tld}'."
            )

    return email


class UserCreate(BaseModel):
    """Schema for user registration."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    recaptcha_token: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Normalize email to lowercase and validate domain."""
        email = v.lower().strip()
        return validate_email_domain(email)

    @field_validator("first_name")
    @classmethod
    def normalize_first_name(cls, v: str) -> str:
        """Normalize first name to Title Case."""
        return v.strip().title()

    @field_validator("last_name")
    @classmethod
    def normalize_last_name(cls, v: str) -> str:
        """Normalize last name to UPPERCASE."""
        return v.strip().upper()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    """Schema for user login."""

    email: EmailStr
    password: str
    is_mobile: bool = False  # For longer-lived mobile tokens
    recaptcha_token: Optional[str] = None

    @field_validator("email")
    @classmethod
    def email_to_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class TokenResponse(BaseModel):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires


class TokenRefresh(BaseModel):
    """Schema for token refresh request."""

    refresh_token: str


class UserProfileResponse(BaseModel):
    """Schema for user profile response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    phone: Optional[str] = None
    bio: Optional[str] = None
    gender: Optional[str] = None
    profile_picture_url: Optional[str] = None
    profile_picture_status: str = "approved"  # pending, approved, rejected
    roles: List[str] = []
    country_id: Optional[int] = None
    city_id: Optional[int] = None
    country_name: Optional[str] = None
    city_name: Optional[str] = None
    # Social links (PRO feature)
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    tiktok_url: Optional[str] = None
    youtube_url: Optional[str] = None
    # Privacy
    is_profile_public: bool = True
    # Social stats
    follower_count: int = 0
    following_count: int = 0
    created_at: datetime

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class UserResponse(BaseModel):
    """Schema for user account response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool
    is_verified: bool
    is_pro: bool = False
    has_password: bool = True  # True for email accounts, False for social-only
    created_at: datetime
    last_login: Optional[datetime] = None
    profile: Optional[UserProfileResponse] = None


class UserListResponse(BaseModel):
    """Paginated user list response."""

    items: List[UserResponse]
    total: int
    page: int
    page_size: int
    pages: int


class UserProfileUpdate(BaseModel):
    """Schema for updating user profile."""

    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    bio: Optional[str] = Field(None, max_length=500)
    gender: Optional[str] = Field(None, pattern="^(male|female|other|prefer_not_to_say)$")
    profile_picture_url: Optional[str] = Field(None, max_length=500)
    country_id: Optional[int] = None
    city_id: Optional[int] = None
    # Social links (PRO feature - validated in endpoint)
    facebook_url: Optional[str] = Field(None, max_length=500)
    instagram_url: Optional[str] = Field(None, max_length=500)
    tiktok_url: Optional[str] = Field(None, max_length=500)
    youtube_url: Optional[str] = Field(None, max_length=500)
    # Privacy setting (any user can set)
    is_profile_public: Optional[bool] = None

    @field_validator("first_name")
    @classmethod
    def normalize_first_name(cls, v: Optional[str]) -> Optional[str]:
        """Normalize first name to Title Case."""
        if v is None:
            return None
        return v.strip().title()

    @field_validator("last_name")
    @classmethod
    def normalize_last_name(cls, v: Optional[str]) -> Optional[str]:
        """Normalize last name to UPPERCASE."""
        if v is None:
            return None
        return v.strip().upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate phone number format with country code.
        Expected format: +[country_code][number] (E.164 format)
        Examples: +40721234567, +1234567890, +44 7911 123456
        """
        if v is None or v == "":
            return None

        # Remove spaces for validation
        phone_clean = v.replace(" ", "").replace("-", "")

        # Must start with +
        if not phone_clean.startswith("+"):
            raise ValueError(
                "Phone number must include country code starting with '+'. "
                "Example: +40721234567 or +1 234 567 8901"
            )

        # After +, only digits allowed
        digits_only = phone_clean[1:]
        if not digits_only.isdigit():
            raise ValueError(
                "Phone number must contain only digits after the country code. "
                "Example: +40721234567"
            )

        # E.164 format: 8-15 digits total (including country code)
        if len(digits_only) < 8 or len(digits_only) > 15:
            raise ValueError(
                "Phone number must be 8-15 digits (including country code). "
                "Example: +40721234567"
            )

        return phone_clean  # Return normalized (no spaces)

    @field_validator("facebook_url", "instagram_url", "tiktok_url", "youtube_url")
    @classmethod
    def validate_social_url(cls, v: Optional[str]) -> Optional[str]:
        """Validate social media URL format."""
        if v is None or v == "":
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class AdminUserProfileUpdate(BaseModel):
    """Schema for admin updating user profile. More fields than regular update."""

    # Account fields (admin only)
    email: Optional[EmailStr] = None
    is_verified: Optional[bool] = None

    # Profile fields
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    bio: Optional[str] = Field(None, max_length=500)
    gender: Optional[str] = Field(None, pattern="^(male|female|other|prefer_not_to_say)$")
    profile_picture_url: Optional[str] = Field(None, max_length=500)
    country_id: Optional[int] = None
    city_id: Optional[int] = None

    # Social links
    facebook_url: Optional[str] = Field(None, max_length=500)
    instagram_url: Optional[str] = Field(None, max_length=500)
    tiktok_url: Optional[str] = Field(None, max_length=500)
    youtube_url: Optional[str] = Field(None, max_length=500)

    # Privacy
    is_profile_public: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def email_to_lowercase(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.lower().strip()

    @field_validator("first_name")
    @classmethod
    def normalize_first_name(cls, v: Optional[str]) -> Optional[str]:
        """Normalize first name to Title Case."""
        if v is None:
            return None
        return v.strip().title()

    @field_validator("last_name")
    @classmethod
    def normalize_last_name(cls, v: Optional[str]) -> Optional[str]:
        """Normalize last name to UPPERCASE."""
        if v is None:
            return None
        return v.strip().upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate phone number format with country code.
        Expected format: +[country_code][number] (E.164 format)
        """
        if v is None or v == "":
            return None

        phone_clean = v.replace(" ", "").replace("-", "")

        if not phone_clean.startswith("+"):
            raise ValueError(
                "Phone number must include country code starting with '+'. "
                "Example: +40721234567 or +1 234 567 8901"
            )

        digits_only = phone_clean[1:]
        if not digits_only.isdigit():
            raise ValueError(
                "Phone number must contain only digits after the country code. "
                "Example: +40721234567"
            )

        if len(digits_only) < 8 or len(digits_only) > 15:
            raise ValueError(
                "Phone number must be 8-15 digits (including country code). "
                "Example: +40721234567"
            )

        return phone_clean

    @field_validator("facebook_url", "instagram_url", "tiktok_url", "youtube_url")
    @classmethod
    def validate_social_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class PasswordChange(BaseModel):
    """Schema for password change."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class PasswordReset(BaseModel):
    """Schema for password reset request."""

    email: EmailStr
    recaptcha_token: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Normalize email to lowercase and validate domain."""
        email = v.lower().strip()
        return validate_email_domain(email)


class PasswordResetConfirm(BaseModel):
    """Schema for password reset confirmation."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=100)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class ActivationRequest(BaseModel):
    """Schema for account activation request."""

    token: str


class ResendVerificationRequest(BaseModel):
    """Schema for resending verification email."""

    email: EmailStr

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Normalize email to lowercase and validate domain."""
        email = v.lower().strip()
        return validate_email_domain(email)


# ============== Notification Preferences Schemas ==============


class NotificationPreferencesResponse(BaseModel):
    """Schema for notification preferences response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    # Event discovery preferences
    notify_events_from_country: bool = True
    notify_event_types: List[int] = []  # empty = all event types
    notify_from_clubs: List[int] = []  # club IDs
    # Event participation preferences
    notify_event_catches: str = "all"  # 'all', 'mine', 'none'
    created_at: datetime
    updated_at: datetime


class NotificationPreferencesUpdate(BaseModel):
    """Schema for updating notification preferences."""

    # Event discovery preferences
    notify_events_from_country: Optional[bool] = None
    notify_event_types: Optional[List[int]] = None
    notify_from_clubs: Optional[List[int]] = None
    # Event participation preferences
    notify_event_catches: Optional[str] = Field(None, pattern="^(all|mine|none)$")


# ============== Device Token Schemas ==============


class DeviceTokenRegister(BaseModel):
    """Schema for registering a device token."""

    token: str = Field(..., min_length=1, max_length=500)
    device_type: str = Field(..., pattern="^(ios|android|web)$")


class DeviceTokenResponse(BaseModel):
    """Schema for device token response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    token: str
    device_type: str
    created_at: datetime
