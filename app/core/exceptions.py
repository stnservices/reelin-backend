"""Custom exception classes for ReelIn application."""

from typing import Any, Optional


class ReelInException(Exception):
    """Base exception for ReelIn application."""

    def __init__(
        self,
        message: str = "An error occurred",
        status_code: int = 500,
        details: Optional[dict[str, Any]] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(ReelInException):
    """Authentication failed."""

    def __init__(
        self,
        message: str = "Authentication failed",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, status_code=401, details=details)


class AuthorizationError(ReelInException):
    """User lacks required permissions."""

    def __init__(
        self,
        message: str = "Permission denied",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, status_code=403, details=details)


class NotFoundError(ReelInException):
    """Resource not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        resource: Optional[str] = None,
        resource_id: Optional[Any] = None,
    ):
        details = {}
        if resource:
            details["resource"] = resource
        if resource_id is not None:
            details["resource_id"] = resource_id
        super().__init__(message=message, status_code=404, details=details)


class ValidationError(ReelInException):
    """Validation error."""

    def __init__(
        self,
        message: str = "Validation failed",
        errors: Optional[list[dict[str, Any]]] = None,
    ):
        details = {"errors": errors} if errors else {}
        super().__init__(message=message, status_code=422, details=details)


class ConflictError(ReelInException):
    """Resource conflict (e.g., duplicate entry)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, status_code=409, details=details)


class RateLimitError(ReelInException):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[int] = None,
    ):
        details = {"retry_after": retry_after} if retry_after else {}
        super().__init__(message=message, status_code=429, details=details)


class StatusTransitionError(ReelInException):
    """Invalid status transition."""

    def __init__(
        self,
        message: str = "Invalid status transition",
        current_status: Optional[str] = None,
        target_status: Optional[str] = None,
        allowed_transitions: Optional[list[str]] = None,
    ):
        details: dict[str, Any] = {}
        if current_status:
            details["current_status"] = current_status
        if target_status:
            details["target_status"] = target_status
        if allowed_transitions:
            details["allowed_transitions"] = allowed_transitions
        super().__init__(message=message, status_code=400, details=details)


class PreconditionFailedError(ReelInException):
    """Precondition for operation not met."""

    def __init__(
        self,
        message: str = "Precondition failed",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message=message, status_code=409, details=details)
