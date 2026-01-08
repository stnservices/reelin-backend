"""Tests for event achievement triggers.

Tests cover:
- Format code mapping (get_format_code)
- TA event completion achievement processing
- Format code passed correctly to achievement engine
- Stats updated before achievements checked
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.utils.event_formats import get_format_code, get_event_participant_ids
from app.tasks.achievement_processing import _process_format_event_achievements


class TestGetFormatCode:
    """Tests for get_format_code helper function."""

    def test_none_event_type_returns_none(self):
        """None event type returns None."""
        result = get_format_code(None)
        assert result is None

    def test_trout_area_code_returns_ta(self):
        """Event type with trout_area code returns 'ta'."""
        event_type = MagicMock()
        event_type.code = "trout_area"
        event_type.name = "Trout Area Competition"

        result = get_format_code(event_type)
        assert result == "ta"

    def test_trout_area_name_returns_ta(self):
        """Event type with 'trout area' in name returns 'ta'."""
        event_type = MagicMock()
        event_type.code = "custom"
        event_type.name = "Trout Area Custom"

        result = get_format_code(event_type)
        assert result == "ta"

    def test_street_fishing_returns_sf(self):
        """Standard event type returns 'sf' (default)."""
        event_type = MagicMock()
        event_type.code = "street_fishing"
        event_type.name = "Street Fishing"

        result = get_format_code(event_type)
        assert result == "sf"

    def test_unknown_type_returns_sf(self):
        """Unknown event type defaults to 'sf'."""
        event_type = MagicMock()
        event_type.code = "custom_type"
        event_type.name = "Custom Competition"

        result = get_format_code(event_type)
        assert result == "sf"

    def test_none_code_and_name_handles_gracefully(self):
        """Event type with None code/name handles gracefully."""
        event_type = MagicMock()
        event_type.code = None
        event_type.name = None

        result = get_format_code(event_type)
        assert result == "sf"


class TestGetEventParticipantIds:
    """Tests for get_event_participant_ids function."""

    @pytest.mark.asyncio
    async def test_ta_format_queries_ta_lineup(self):
        """TA format queries TALineup table."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1,), (2,), (3,)]
        mock_db.execute.return_value = mock_result

        with patch("app.utils.event_formats.TALineup"):
            result = await get_event_participant_ids(mock_db, 1, "ta")
            assert result == [1, 2, 3]
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_sf_format_queries_event_participant(self):
        """SF format queries EventParticipant table."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(6,), (7,), (8,), (9,)]
        mock_db.execute.return_value = mock_result

        with patch("app.utils.event_formats.EventParticipant"):
            result = await get_event_participant_ids(mock_db, 1, "sf")
            assert result == [6, 7, 8, 9]
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        """Empty query result returns empty list."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute.return_value = mock_result

        with patch("app.utils.event_formats.TALineup"):
            result = await get_event_participant_ids(mock_db, 1, "ta")
            assert result == []


class TestProcessFormatEventAchievements:
    """Tests for _process_format_event_achievements function."""

    @pytest.mark.asyncio
    async def test_event_not_found_returns_not_found_status(self):
        """Event not found returns not_found status."""
        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session:
            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event query returning None
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_db.execute.return_value = mock_result

            result = await _process_format_event_achievements(999, "ta")

            assert result["status"] == "not_found"
            assert result["event_id"] == 999
            assert result["awarded"] == 0

    @pytest.mark.asyncio
    async def test_processes_all_participants(self):
        """Achievement check is called for all participants."""
        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session, \
             patch("app.tasks.achievement_processing.get_event_participant_ids") as mock_get_participants, \
             patch("app.tasks.achievement_processing.achievement_service") as mock_achievement, \
             patch("app.tasks.achievement_processing.statistics_service") as mock_stats:

            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event exists
            mock_event = MagicMock()
            mock_event.event_type = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_event
            mock_db.execute.return_value = mock_result

            # Mock 3 participants
            mock_get_participants.return_value = [1, 2, 3]

            # Mock stats update
            mock_stats.update_user_stats_for_event = AsyncMock()

            # Mock no achievements awarded
            mock_achievement.check_and_award_achievements = AsyncMock(return_value=[])

            result = await _process_format_event_achievements(1, "ta")

            assert result["status"] == "completed"
            assert result["participants"] == 3
            assert mock_stats.update_user_stats_for_event.call_count == 3
            assert mock_achievement.check_and_award_achievements.call_count == 3

    @pytest.mark.asyncio
    async def test_format_code_passed_to_achievement_engine(self):
        """Format code is correctly passed to achievement engine."""
        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session, \
             patch("app.tasks.achievement_processing.get_event_participant_ids") as mock_get_participants, \
             patch("app.tasks.achievement_processing.achievement_service") as mock_achievement, \
             patch("app.tasks.achievement_processing.statistics_service") as mock_stats:

            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event exists
            mock_event = MagicMock()
            mock_event.event_type = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_event
            mock_db.execute.return_value = mock_result

            # Mock 1 participant
            mock_get_participants.return_value = [1]

            # Mock stats update
            mock_stats.update_user_stats_for_event = AsyncMock()

            # Mock achievement check
            mock_achievement.check_and_award_achievements = AsyncMock(return_value=[])

            await _process_format_event_achievements(1, "ta")

            # Verify format_code was passed
            call_kwargs = mock_achievement.check_and_award_achievements.call_args[1]
            assert call_kwargs["format_code"] == "ta"
            assert call_kwargs["trigger"] == "event_completed"

    @pytest.mark.asyncio
    async def test_stats_updated_before_achievements_checked(self):
        """Stats are updated before achievement check for each user."""
        call_order = []

        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session, \
             patch("app.tasks.achievement_processing.get_event_participant_ids") as mock_get_participants, \
             patch("app.tasks.achievement_processing.achievement_service") as mock_achievement, \
             patch("app.tasks.achievement_processing.statistics_service") as mock_stats:

            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event exists
            mock_event = MagicMock()
            mock_event.event_type = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_event
            mock_db.execute.return_value = mock_result

            # Mock 1 participant
            mock_get_participants.return_value = [1]

            # Track call order
            async def track_stats(*args, **kwargs):
                call_order.append("stats")

            async def track_achievements(*args, **kwargs):
                call_order.append("achievements")
                return []

            mock_stats.update_user_stats_for_event = track_stats
            mock_achievement.check_and_award_achievements = track_achievements

            await _process_format_event_achievements(1, "ta")

            # Stats should be called before achievements
            assert call_order == ["stats", "achievements"]

    @pytest.mark.asyncio
    async def test_continues_processing_on_single_user_error(self):
        """Processing continues even if one user fails."""
        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session, \
             patch("app.tasks.achievement_processing.get_event_participant_ids") as mock_get_participants, \
             patch("app.tasks.achievement_processing.achievement_service") as mock_achievement, \
             patch("app.tasks.achievement_processing.statistics_service") as mock_stats:

            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event exists
            mock_event = MagicMock()
            mock_event.event_type = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_event
            mock_db.execute.return_value = mock_result

            # Mock 3 participants
            mock_get_participants.return_value = [1, 2, 3]

            # First user fails, others succeed
            call_count = [0]

            async def mock_check(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("User 1 failed")
                return []

            mock_stats.update_user_stats_for_event = AsyncMock()
            mock_achievement.check_and_award_achievements = mock_check

            result = await _process_format_event_achievements(1, "ta")

            # Should have processed all 3 users despite first error
            assert result["participants"] == 3
            assert result["errors"] == 1
            assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_counts_awarded_achievements(self):
        """Total awarded achievements is correctly counted."""
        with patch("app.tasks.achievement_processing.async_session_maker") as mock_session, \
             patch("app.tasks.achievement_processing.get_event_participant_ids") as mock_get_participants, \
             patch("app.tasks.achievement_processing.achievement_service") as mock_achievement, \
             patch("app.tasks.achievement_processing.statistics_service") as mock_stats, \
             patch("app.tasks.achievement_processing.send_achievement_notification"):

            mock_db = AsyncMock()
            mock_session.return_value.__aenter__.return_value = mock_db

            # Mock event exists
            mock_event = MagicMock()
            mock_event.event_type = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_event
            mock_db.execute.return_value = mock_result

            # Mock 2 participants
            mock_get_participants.return_value = [1, 2]

            mock_stats.update_user_stats_for_event = AsyncMock()

            # User 1 gets 2 achievements, User 2 gets 1
            achievement1 = MagicMock(id=1, code="match_master_bronze")
            achievement2 = MagicMock(id=2, code="ta_champion")
            achievement3 = MagicMock(id=3, code="match_master_silver")

            call_count = [0]

            async def mock_check(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [achievement1, achievement2]
                return [achievement3]

            mock_achievement.check_and_award_achievements = mock_check

            result = await _process_format_event_achievements(1, "ta")

            assert result["awarded"] == 3
