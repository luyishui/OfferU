from __future__ import annotations

from typing import Any


class OperatorError(Exception):
    """Structured operator error that can be returned as JSON."""

    def __init__(self, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_response(self) -> dict[str, Any]:
        return _error_response(self.code, self.message, self.details)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _error_response(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(message),
            "details": _json_safe(details or {}),
        },
    }


def validation_error(message: str, details: Any | None = None) -> dict[str, Any]:
    return _error_response("validation_error", message, details)


def permission_error(message: str, details: Any | None = None) -> dict[str, Any]:
    return _error_response("permission_error", message, details)


def not_found_error(message: str, details: Any | None = None) -> dict[str, Any]:
    return _error_response("not_found_error", message, details)


def conflict_error(message: str, details: Any | None = None) -> dict[str, Any]:
    return _error_response("conflict_error", message, details)


def transient_error(message: str, details: Any | None = None) -> dict[str, Any]:
    return _error_response("transient_error", message, details)
