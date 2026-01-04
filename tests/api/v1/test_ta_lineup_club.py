"""Tests for TALineup club_id feature.

Tests that TA lineups capture the user's active club membership at enrollment time.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.trout_area import TALineup
from app.models.club import Club, ClubMembership, MembershipStatus
from app.models.user import UserAccount, UserProfile
from app.api.v1.trout_area import get_user_active_club_id, get_club_ids_for_users


@pytest.mark.asyncio
async def test_ta_lineup_has_club_id_field(db_session: AsyncSession):
    """Test that TALineup model accepts club_id field."""
    # Create a minimal lineup object (won't be saved, just checking field exists)
    lineup = TALineup(
        event_id=1,
        draw_number=1,
        sector=1,
        is_ghost=False,
        club_id=123,  # This should work without error
    )
    assert lineup.club_id == 123


@pytest.mark.asyncio
async def test_ta_lineup_accepts_null_club_id(db_session: AsyncSession):
    """Test that TALineup accepts null club_id for users without clubs."""
    lineup = TALineup(
        event_id=1,
        draw_number=1,
        sector=1,
        is_ghost=False,
        club_id=None,
    )
    assert lineup.club_id is None


@pytest.mark.asyncio
async def test_get_user_active_club_id_returns_club(db_session: AsyncSession):
    """Test helper function returns active club_id for club member."""
    # Create a user
    user = UserAccount(email="test@example.com", hashed_password="hash")
    db_session.add(user)
    await db_session.flush()

    # Create a club
    club = Club(name="Test Club", owner_id=user.id)
    db_session.add(club)
    await db_session.flush()

    # Create active membership
    membership = ClubMembership(
        user_id=user.id,
        club_id=club.id,
        status=MembershipStatus.ACTIVE.value,
    )
    db_session.add(membership)
    await db_session.commit()

    # Test helper function
    result = await get_user_active_club_id(db_session, user.id)
    assert result == club.id


@pytest.mark.asyncio
async def test_get_user_active_club_id_returns_none_for_non_member(db_session: AsyncSession):
    """Test helper returns None for user without club membership."""
    # Create a user without any club membership
    user = UserAccount(email="noclub@example.com", hashed_password="hash")
    db_session.add(user)
    await db_session.commit()

    result = await get_user_active_club_id(db_session, user.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_club_ids_for_users_batch(db_session: AsyncSession):
    """Test batch helper returns club_ids for multiple users."""
    # Create users
    user1 = UserAccount(email="user1@example.com", hashed_password="hash")
    user2 = UserAccount(email="user2@example.com", hashed_password="hash")
    user3 = UserAccount(email="user3@example.com", hashed_password="hash")  # No club
    db_session.add_all([user1, user2, user3])
    await db_session.flush()

    # Create clubs
    club1 = Club(name="Club 1", owner_id=user1.id)
    club2 = Club(name="Club 2", owner_id=user2.id)
    db_session.add_all([club1, club2])
    await db_session.flush()

    # Create memberships
    membership1 = ClubMembership(
        user_id=user1.id,
        club_id=club1.id,
        status=MembershipStatus.ACTIVE.value,
    )
    membership2 = ClubMembership(
        user_id=user2.id,
        club_id=club2.id,
        status=MembershipStatus.ACTIVE.value,
    )
    db_session.add_all([membership1, membership2])
    await db_session.commit()

    # Test batch function
    result = await get_club_ids_for_users(db_session, [user1.id, user2.id, user3.id])

    assert result[user1.id] == club1.id
    assert result[user2.id] == club2.id
    assert result[user3.id] is None


@pytest.mark.asyncio
async def test_get_user_active_club_id_ignores_pending_membership(db_session: AsyncSession):
    """Test that pending memberships are ignored."""
    # Create user
    user = UserAccount(email="pending@example.com", hashed_password="hash")
    db_session.add(user)
    await db_session.flush()

    # Create club
    club = Club(name="Pending Club", owner_id=user.id)
    db_session.add(club)
    await db_session.flush()

    # Create pending (not active) membership
    membership = ClubMembership(
        user_id=user.id,
        club_id=club.id,
        status=MembershipStatus.PENDING.value,
    )
    db_session.add(membership)
    await db_session.commit()

    result = await get_user_active_club_id(db_session, user.id)
    assert result is None  # Should be None since membership is pending
