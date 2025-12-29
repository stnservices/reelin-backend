"""Social authentication service for handling OAuth callbacks."""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import UserAccount, UserProfile
from app.models.social_account import SocialAccount, OAuthProvider


class SocialAuthService:
    """Service for handling social authentication logic."""

    @staticmethod
    async def get_or_create_user_from_oauth(
        db: AsyncSession,
        provider: OAuthProvider,
        provider_account_id: str,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> UserAccount:
        """
        Get or create a user from OAuth callback.

        Logic:
        1. Check if social account already exists -> return linked user
        2. Check if user with email exists -> link social account
        3. Create new user -> link social account
        """
        # 1. Check if social account already linked
        social_query = (
            select(SocialAccount)
            .options(selectinload(SocialAccount.user).selectinload(UserAccount.profile))
            .where(
                SocialAccount.provider == provider,
                SocialAccount.provider_account_id == provider_account_id,
            )
        )
        result = await db.execute(social_query)
        social_account = result.scalar_one_or_none()

        if social_account:
            # Update tokens if provided
            if access_token:
                social_account.access_token = access_token
            if refresh_token:
                social_account.refresh_token = refresh_token

            # Update profile name if provided (Apple only sends on first sign-in)
            user = social_account.user
            if user.profile and (first_name or last_name):
                email_prefix = email.split("@")[0] if email else ""
                if first_name and (not user.profile.first_name or user.profile.first_name == email_prefix):
                    user.profile.first_name = first_name
                if last_name and not user.profile.last_name:
                    user.profile.last_name = last_name

            await db.commit()
            await db.refresh(social_account.user, ["profile"])
            return social_account.user

        # 2. Check if user with email exists
        user_query = (
            select(UserAccount)
            .options(selectinload(UserAccount.profile))
            .where(UserAccount.email == email)
        )
        result = await db.execute(user_query)
        user = result.scalar_one_or_none()

        if not user:
            # 3. Create new user (no password for social-only)
            user = UserAccount(
                email=email,
                password_hash=None,  # Social-only account
                avatar_url=avatar_url,
                is_active=True,
                is_verified=True,  # Social accounts are pre-verified
            )
            db.add(user)
            await db.flush()

            # Create user profile with default "angler" role
            profile = UserProfile(
                user_id=user.id,
                first_name=first_name or email.split("@")[0],
                last_name=last_name or "",
                roles=["angler"],
            )
            db.add(profile)
        else:
            # Update avatar if not set and provided from social
            if avatar_url and not user.avatar_url:
                user.avatar_url = avatar_url

            # Update profile name if provided and profile exists but name is missing/email-derived
            # This is important for Apple Sign-In which only sends name on first sign-in
            if user.profile and (first_name or last_name):
                email_prefix = email.split("@")[0] if email else ""
                # Update first name if provided and current is empty or email-derived
                if first_name and (not user.profile.first_name or user.profile.first_name == email_prefix):
                    user.profile.first_name = first_name
                # Update last name if provided and current is empty
                if last_name and not user.profile.last_name:
                    user.profile.last_name = last_name

        # Link social account to user
        new_social_account = SocialAccount(
            user_id=user.id,
            provider=provider,
            provider_account_id=provider_account_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        db.add(new_social_account)
        await db.commit()
        await db.refresh(user, ["profile"])

        return user

    @staticmethod
    async def get_user_social_accounts(
        db: AsyncSession,
        user_id: int,
    ) -> list[SocialAccount]:
        """Get all social accounts linked to a user."""
        query = select(SocialAccount).where(SocialAccount.user_id == user_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def unlink_social_account(
        db: AsyncSession,
        user_id: int,
        social_account_id: int,
    ) -> bool:
        """
        Unlink a social account from user.

        Validates that user has another auth method (password or other social).
        """
        # Get user with social accounts
        user_query = (
            select(UserAccount)
            .options(selectinload(UserAccount.social_accounts))
            .where(UserAccount.id == user_id)
        )
        result = await db.execute(user_query)
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("User not found")

        # Find the social account to unlink
        target_account = None
        for sa in user.social_accounts:
            if sa.id == social_account_id:
                target_account = sa
                break

        if not target_account:
            raise ValueError("Social account not found")

        # Validate user has another auth method
        has_password = user.password_hash is not None
        has_other_social = len(user.social_accounts) > 1

        if not has_password and not has_other_social:
            raise ValueError(
                "Cannot unlink the only authentication method. "
                "Set a password or link another social account first."
            )

        # Delete the social account
        await db.delete(target_account)
        await db.commit()
        return True
