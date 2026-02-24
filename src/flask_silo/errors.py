"""Custom exceptions for Flask-Silo.

All exceptions inherit from :class:`SiloError`, making it easy to catch
any library-specific error with a single ``except SiloError`` clause.
"""

from __future__ import annotations


class SiloError(Exception):
    """Base exception for all Flask-Silo errors."""


class SessionExpired(SiloError):
    """Raised when accessing a session that has expired due to TTL.

    Attributes:
        sid: The expired session identifier.
    """

    def __init__(self, sid: str, message: str | None = None) -> None:
        self.sid = sid
        super().__init__(message or f"Session '{sid}' has expired due to inactivity.")


class SessionBusy(SiloError):
    """Raised when attempting to modify a session that has active tasks.

    Attributes:
        sid: The busy session identifier.
    """

    def __init__(self, sid: str, message: str | None = None) -> None:
        self.sid = sid
        super().__init__(
            message or f"Session '{sid}' has active tasks and cannot be modified."
        )


class NamespaceError(SiloError):
    """Raised when referencing an unregistered namespace.

    Attributes:
        namespace: The unregistered namespace name.
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(
            f"Namespace '{namespace}' is not registered. Call register() first."
        )
