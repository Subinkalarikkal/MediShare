"""Service-layer exceptions. Pages translate these into user-facing messages."""
from utils.validators import ValidationError  # re-export

__all__ = ["ServiceError", "PermissionDenied", "NotFound", "WorkflowError", "SafetyViolation",
           "ValidationError"]


class ServiceError(Exception):
    pass


class PermissionDenied(ServiceError):
    pass


class NotFound(ServiceError):
    pass


class WorkflowError(ServiceError):
    """Invalid state transition or business-rule failure."""


class SafetyViolation(WorkflowError):
    """An action would break a mandatory safety / regulatory restriction."""
