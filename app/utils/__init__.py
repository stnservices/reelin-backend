"""Utility modules for the ReelIn backend."""

from .lifecycle_guards import (
    require_modifiable_status,
    LifecycleError,
)
from .event_formats import (
    get_format_code,
    get_event_participant_ids,
)
from .errors import (
    ErrorCode,
    format_error_response,
    format_field_errors,
    format_lifecycle_error,
    get_error_code_for_exception,
)

def is_test_account(email: str | None) -> bool:
    """Test/internal accounts use @test.reelin.ro emails."""
    return bool(email and email.endswith("@test.reelin.ro"))


__all__ = [
    "require_modifiable_status",
    "LifecycleError",
    "get_format_code",
    "get_event_participant_ids",
    "ErrorCode",
    "format_error_response",
    "format_field_errors",
    "format_lifecycle_error",
    "get_error_code_for_exception",
    "is_test_account",
]
