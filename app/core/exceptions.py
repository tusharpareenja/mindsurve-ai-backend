"""Shared domain exceptions mapped to HTTP at the API edge."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(message, status_code=404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "You do not have access to this resource.") -> None:
        super().__init__(message, status_code=403)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict.") -> None:
        super().__init__(message, status_code=409)
