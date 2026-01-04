"""
Standardized error response utilities.

This module provides consistent error response formatting for the API.
All errors follow the structure: {code, message, details}
"""

from typing import Any, Optional


class ErrorCode:
    """Standard error codes for API responses."""

    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Lifecycle/state errors
    LIFECYCLE_ERROR = "LIFECYCLE_ERROR"

    # Authentication/Authorization
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"

    # Resource errors
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"

    # Rate limiting
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"

    # Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"


def format_error_response(
    code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Format a standardized error response.

    Args:
        code: Error code from ErrorCode class
        message: Human-readable message or i18n key
        details: Additional error details (field errors, context, etc.)

    Returns:
        Standardized error response dict
    """
    response: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if details:
        response["details"] = details
    return response


def format_field_errors(field_errors: dict[str, str]) -> dict[str, Any]:
    """
    Format field-level validation errors.

    Args:
        field_errors: Dict mapping field names to error messages

    Returns:
        Details dict with fields structure
    """
    return {"fields": field_errors}


def format_lifecycle_error(
    current_status: str,
    target_status: Optional[str] = None,
    allowed_transitions: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Format lifecycle/status transition error details.

    Args:
        current_status: Current status of the resource
        target_status: Attempted target status (optional)
        allowed_transitions: List of allowed transitions (optional)

    Returns:
        Details dict with lifecycle context
    """
    details: dict[str, Any] = {"current_status": current_status}
    if target_status:
        details["target_status"] = target_status
    if allowed_transitions:
        details["allowed_transitions"] = allowed_transitions
    return details


# Error code mapping for existing exception types
EXCEPTION_CODE_MAP = {
    "ValidationError": ErrorCode.VALIDATION_ERROR,
    "StatusTransitionError": ErrorCode.LIFECYCLE_ERROR,
    "AuthenticationError": ErrorCode.AUTHENTICATION_ERROR,
    "AuthorizationError": ErrorCode.AUTHORIZATION_ERROR,
    "NotFoundError": ErrorCode.NOT_FOUND,
    "ConflictError": ErrorCode.CONFLICT,
    "PreconditionFailedError": ErrorCode.LIFECYCLE_ERROR,
    "RateLimitError": ErrorCode.RATE_LIMIT_ERROR,
    "ReelInException": ErrorCode.INTERNAL_ERROR,
}


def get_error_code_for_exception(exc_class_name: str) -> str:
    """
    Get the appropriate error code for an exception class.

    Args:
        exc_class_name: Name of the exception class

    Returns:
        Appropriate ErrorCode constant
    """
    return EXCEPTION_CODE_MAP.get(exc_class_name, ErrorCode.INTERNAL_ERROR)
