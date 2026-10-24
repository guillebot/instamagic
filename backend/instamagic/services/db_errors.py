"""Detect database connection failures for retry / job failure handling."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, OperationalError


def is_db_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated:
            return True
        orig = exc.orig
        if orig is not None:
            name = type(orig).__name__
            if "OperationalError" in name or "InterfaceError" in name:
                return True
            message = str(orig).lower()
            if "connection" in message and ("closed" in message or "lost" in message or "unexpected" in message):
                return True
    return False
