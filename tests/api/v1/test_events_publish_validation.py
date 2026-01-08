"""Tests for event publish readiness validation.

Tests the PublishValidationService and the /api/v1/events/{id}/publish-readiness endpoint.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, EventFishScoring, EventType, ScoringConfig
from app.models.trout_area import TAEventSettings
from app.services.publish_validation import (
    PublishValidationService,
    ValidationKeys,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def validation_service(mock_db):
    """Create a validation service instance with mock db."""
    return PublishValidationService(mock_db)


@pytest.fixture
def future_date():
    """Return a date in the future."""
    return datetime.now(timezone.utc) + timedelta(days=30)


@pytest.fixture
def past_date():
    """Return a date in the past."""
    return datetime.now(timezone.utc) - timedelta(days=1)


@pytest.fixture
def sf_event_type():
    """Create a Street Fishing event type."""
    event_type = MagicMock(spec=EventType)
    event_type.code = "sf"
    event_type.format_code = "sf"
    return event_type


@pytest.fixture
def ta_event_type():
    """Create a Trout Area event type."""
    event_type = MagicMock(spec=EventType)
    event_type.code = "ta"
    event_type.format_code = "ta"
    return event_type


@pytest.fixture
def valid_sf_event(sf_event_type, future_date):
    """Create a valid SF event with all required fields."""
    event = MagicMock(spec=Event)
    event.id = 1
    event.name = "Test SF Event"
    event.location = "Test Location"
    event.start_date = future_date
    event.end_date = future_date + timedelta(hours=8)
    event.event_type = sf_event_type
    event.scoring_config = MagicMock(spec=ScoringConfig)
    event.ta_settings = None
    event.is_deleted = False
    return event


@pytest.fixture
def valid_ta_event(ta_event_type, future_date):
    """Create a valid TA event with all required fields."""
    event = MagicMock(spec=Event)
    event.id = 2
    event.name = "Test TA Event"
    event.location = "Test Location"
    event.start_date = future_date
    event.end_date = future_date + timedelta(hours=8)
    event.event_type = ta_event_type
    event.scoring_config = None

    # TA settings
    ta_settings = MagicMock(spec=TAEventSettings)
    ta_settings.number_of_legs = 5
    ta_settings.match_duration_minutes = 60
    event.ta_settings = ta_settings
    event.is_deleted = False
    return event


# =============================================================================
# Common Validation Tests
# =============================================================================


class TestCommonValidation:
    """Tests for common validation checks (all event types)."""

    def test_missing_name(self, validation_service, valid_sf_event):
        """Test validation fails when name is missing."""
        valid_sf_event.name = ""
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_NAME in missing
        assert checks["has_name"] is False

    def test_missing_name_none(self, validation_service, valid_sf_event):
        """Test validation fails when name is None."""
        valid_sf_event.name = None
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_NAME in missing
        assert checks["has_name"] is False

    def test_missing_name_whitespace(self, validation_service, valid_sf_event):
        """Test validation fails when name is whitespace only."""
        valid_sf_event.name = "   "
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_NAME in missing
        assert checks["has_name"] is False

    def test_missing_location(self, validation_service, valid_sf_event):
        """Test validation fails when location is missing."""
        valid_sf_event.location = ""
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_LOCATION in missing
        assert checks["has_location"] is False

    def test_missing_start_date(self, validation_service, valid_sf_event):
        """Test validation fails when start_date is missing."""
        valid_sf_event.start_date = None
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_START_DATE in missing
        assert checks["has_start_date"] is False

    def test_missing_end_date(self, validation_service, valid_sf_event):
        """Test validation fails when end_date is missing."""
        valid_sf_event.end_date = None
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.MISSING_END_DATE in missing
        assert checks["has_end_date"] is False

    def test_start_date_in_past(self, validation_service, valid_sf_event, past_date):
        """Test validation fails when start_date is in the past."""
        valid_sf_event.start_date = past_date
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.START_DATE_IN_PAST in missing
        assert checks["start_date_in_future"] is False

    def test_end_date_before_start(self, validation_service, valid_sf_event, future_date):
        """Test validation fails when end_date is before start_date."""
        valid_sf_event.start_date = future_date + timedelta(days=1)
        valid_sf_event.end_date = future_date
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert ValidationKeys.END_DATE_BEFORE_START in missing
        assert checks["end_date_after_start"] is False

    def test_valid_common_fields(self, validation_service, valid_sf_event):
        """Test all common validations pass for valid event."""
        missing, checks = validation_service._validate_common(valid_sf_event)

        assert len(missing) == 0
        assert checks["has_name"] is True
        assert checks["has_location"] is True
        assert checks["has_start_date"] is True
        assert checks["has_end_date"] is True
        assert checks["start_date_in_future"] is True
        assert checks["end_date_after_start"] is True


# =============================================================================
# SF-Specific Validation Tests
# =============================================================================


class TestSFValidation:
    """Tests for Street Fishing specific validation checks."""

    @pytest.mark.asyncio
    async def test_sf_missing_species(self, validation_service, valid_sf_event, mock_db):
        """Test validation fails when SF event has no species configured."""
        # Mock the database query to return 0 species
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_db.execute.return_value = mock_result

        missing, checks = await validation_service._validate_sf(valid_sf_event)

        assert ValidationKeys.SF_NO_SPECIES in missing
        assert checks["sf_has_species"] is False

    @pytest.mark.asyncio
    async def test_sf_missing_scoring_config(self, validation_service, valid_sf_event, mock_db):
        """Test validation fails when SF event has no scoring config."""
        valid_sf_event.scoring_config = None

        # Mock species count as present
        mock_result = MagicMock()
        mock_result.scalar.return_value = 3
        mock_db.execute.return_value = mock_result

        missing, checks = await validation_service._validate_sf(valid_sf_event)

        assert ValidationKeys.SF_NO_SCORING_CONFIG in missing
        assert checks["sf_has_scoring_config"] is False

    @pytest.mark.asyncio
    async def test_sf_valid_event(self, validation_service, valid_sf_event, mock_db):
        """Test all SF validations pass for valid event."""
        # Mock species count as present
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_db.execute.return_value = mock_result

        missing, checks = await validation_service._validate_sf(valid_sf_event)

        assert len(missing) == 0
        assert checks["sf_has_species"] is True
        assert checks["sf_has_scoring_config"] is True


# =============================================================================
# TA-Specific Validation Tests
# =============================================================================


class TestTAValidation:
    """Tests for Trout Area specific validation checks."""

    def test_ta_missing_settings(self, validation_service, valid_ta_event):
        """Test validation fails when TA event has no settings."""
        valid_ta_event.ta_settings = None

        missing, checks = validation_service._validate_ta(valid_ta_event)

        assert ValidationKeys.TA_NO_SETTINGS in missing
        assert checks["ta_has_settings"] is False
        assert checks["ta_has_legs"] is False

    def test_ta_missing_legs(self, validation_service, valid_ta_event):
        """Test validation fails when TA event has no legs configured."""
        valid_ta_event.ta_settings.number_of_legs = 0

        missing, checks = validation_service._validate_ta(valid_ta_event)

        assert ValidationKeys.TA_NO_LEGS in missing
        assert checks["ta_has_legs"] is False

    def test_ta_legs_none(self, validation_service, valid_ta_event):
        """Test validation fails when TA event legs is None."""
        valid_ta_event.ta_settings.number_of_legs = None

        missing, checks = validation_service._validate_ta(valid_ta_event)

        assert ValidationKeys.TA_NO_LEGS in missing
        assert checks["ta_has_legs"] is False

    def test_ta_valid_event(self, validation_service, valid_ta_event):
        """Test all TA validations pass for valid event."""
        missing, checks = validation_service._validate_ta(valid_ta_event)

        assert len(missing) == 0
        assert checks["ta_has_settings"] is True
        assert checks["ta_has_legs"] is True


# =============================================================================
# Full Validation Tests
# =============================================================================


class TestFullValidation:
    """Tests for the complete validate_publish_readiness method."""

    @pytest.mark.asyncio
    async def test_valid_sf_event_is_ready(self, validation_service, valid_sf_event, mock_db):
        """Test valid SF event returns is_ready=True."""
        # Mock get_event to return our valid event
        with patch.object(
            validation_service, "get_event", return_value=valid_sf_event
        ):
            # Mock species query
            mock_result = MagicMock()
            mock_result.scalar.return_value = 5
            mock_db.execute.return_value = mock_result

            is_ready, missing, checks = await validation_service.validate_publish_readiness(1)

            assert is_ready is True
            assert len(missing) == 0

    @pytest.mark.asyncio
    async def test_invalid_sf_event_not_ready(self, validation_service, valid_sf_event, mock_db):
        """Test SF event without species returns is_ready=False."""
        valid_sf_event.scoring_config = None

        with patch.object(
            validation_service, "get_event", return_value=valid_sf_event
        ):
            # Mock species query - no species
            mock_result = MagicMock()
            mock_result.scalar.return_value = 0
            mock_db.execute.return_value = mock_result

            is_ready, missing, checks = await validation_service.validate_publish_readiness(1)

            assert is_ready is False
            assert ValidationKeys.SF_NO_SPECIES in missing
            assert ValidationKeys.SF_NO_SCORING_CONFIG in missing

    @pytest.mark.asyncio
    async def test_valid_ta_event_is_ready(self, validation_service, valid_ta_event, mock_db):
        """Test valid TA event returns is_ready=True."""
        with patch.object(
            validation_service, "get_event", return_value=valid_ta_event
        ):
            is_ready, missing, checks = await validation_service.validate_publish_readiness(2)

            assert is_ready is True
            assert len(missing) == 0

    @pytest.mark.asyncio
    async def test_invalid_ta_event_not_ready(self, validation_service, valid_ta_event, mock_db):
        """Test TA event without settings returns is_ready=False."""
        valid_ta_event.ta_settings = None

        with patch.object(
            validation_service, "get_event", return_value=valid_ta_event
        ):
            is_ready, missing, checks = await validation_service.validate_publish_readiness(2)

            assert is_ready is False
            assert ValidationKeys.TA_NO_SETTINGS in missing

    @pytest.mark.asyncio
    async def test_common_validation_failures(
        self, validation_service, valid_sf_event, mock_db, past_date
    ):
        """Test common validation failures are returned."""
        valid_sf_event.name = ""
        valid_sf_event.start_date = past_date

        with patch.object(
            validation_service, "get_event", return_value=valid_sf_event
        ):
            # Mock species query
            mock_result = MagicMock()
            mock_result.scalar.return_value = 5
            mock_db.execute.return_value = mock_result

            is_ready, missing, checks = await validation_service.validate_publish_readiness(1)

            assert is_ready is False
            assert ValidationKeys.MISSING_NAME in missing
            assert ValidationKeys.START_DATE_IN_PAST in missing


# =============================================================================
# i18n Key Tests
# =============================================================================


class TestValidationKeys:
    """Tests for i18n message key constants."""

    def test_common_keys_have_correct_prefix(self):
        """Test common keys have validation.publish prefix."""
        assert ValidationKeys.MISSING_NAME.startswith("validation.publish.")
        assert ValidationKeys.MISSING_LOCATION.startswith("validation.publish.")
        assert ValidationKeys.MISSING_START_DATE.startswith("validation.publish.")

    def test_sf_keys_have_correct_prefix(self):
        """Test SF keys have validation.publish.sf prefix."""
        assert ValidationKeys.SF_NO_SPECIES.startswith("validation.publish.sf.")
        assert ValidationKeys.SF_NO_SCORING_CONFIG.startswith("validation.publish.sf.")

    def test_ta_keys_have_correct_prefix(self):
        """Test TA keys have validation.publish.ta prefix."""
        assert ValidationKeys.TA_NO_SETTINGS.startswith("validation.publish.ta.")
        assert ValidationKeys.TA_NO_LEGS.startswith("validation.publish.ta.")
