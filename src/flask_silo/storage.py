"""Pluggable storage backends for Flask-Silo.

:class:`SiloStorage` defines the interface that all backends must implement.
:class:`InMemoryStorage` is the default backend, storing sessions in a
process-local Python dictionary.

To support multi-worker deployments, implement a backend that persists
to an external data store (e.g. :class:`~flask_silo.redis_storage.RedisStorage`).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class SiloStorage(ABC):
    """Abstract base class for session storage backends.

    A storage backend is responsible for persisting session dicts and
    tracking recently-expired session IDs.  It does **not** handle
    application logic like namespace factories, callbacks, or TTL
    enforcement — those responsibilities belong to
    :class:`~flask_silo.store.SessionStore`.

    Implement this interface to plug in a custom storage layer
    (Redis, PostgreSQL, filesystem, etc.).

    All methods are called under :class:`SessionStore`'s internal lock,
    so implementations do **not** need their own locking for
    single-process deployments.  External backends (Redis, etc.) should
    rely on the data store's native atomicity for cross-process safety.
    """

    # -- Session operations ------------------------------------------------

    @abstractmethod
    def get_session(self, sid: str) -> dict[str, Any] | None:
        """Retrieve a session by ID.

        Returns:
            The session dict, or ``None`` if the SID does not exist.

        Note:
            :class:`InMemoryStorage` returns a **reference** to the stored
            dict (mutations are reflected immediately).  External backends
            return a deserialised **copy** — always call :meth:`set_session`
            after mutations for portability.
        """

    @abstractmethod
    def set_session(self, sid: str, data: dict[str, Any]) -> None:
        """Store (create or replace) a session."""

    @abstractmethod
    def delete_session(self, sid: str) -> None:
        """Delete a session.  No-op if the SID does not exist."""

    @abstractmethod
    def has_session(self, sid: str) -> bool:
        """Check whether a session exists."""

    @abstractmethod
    def all_sessions(self) -> list[tuple[str, dict[str, Any]]]:
        """Return a snapshot of all sessions as ``(sid, data)`` pairs.

        The returned list must be safe to iterate while deleting from the
        underlying store (i.e. return a copy, not a live view).
        """

    @abstractmethod
    def session_count(self) -> int:
        """Return the number of active sessions."""

    @abstractmethod
    def all_sids(self) -> list[str]:
        """Return a list of all active session IDs."""

    # -- Expired-SID tracking ----------------------------------------------

    @abstractmethod
    def mark_expired(self, sid: str, timestamp: float) -> None:
        """Record that *sid* was expired at *timestamp*."""

    @abstractmethod
    def is_expired(self, sid: str) -> bool:
        """Check whether *sid* is in the recently-expired set."""

    @abstractmethod
    def clear_expired(self, sid: str) -> None:
        """Remove *sid* from the expired set (e.g. on re-upload)."""

    @abstractmethod
    def prune_expired(self, max_age: float) -> None:
        """Remove expired records older than *max_age* seconds."""

    @abstractmethod
    def expired_count(self) -> int:
        """Return the number of tracked expired SIDs."""


class InMemoryStorage(SiloStorage):
    """In-process dict-based storage (default).

    Stores sessions in a plain Python dictionary and expired SIDs in a
    set of ``(sid, timestamp)`` tuples.  Fast and zero-dependency, but
    limited to a **single process**.

    .. note::

        :meth:`get_session` returns a **reference** to the stored dict,
        so in-place mutations are reflected immediately without calling
        :meth:`set_session`.  Other backends (e.g. Redis) return copies,
        so always call :meth:`set_session` after mutations for
        portability across backends.
    """

    __slots__ = ("_data", "_expired")

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._expired: set[tuple[str, float]] = set()

    # -- Sessions ----------------------------------------------------------

    def get_session(self, sid: str) -> dict[str, Any] | None:
        """Retrieve a session by ID (returns a reference, not a copy)."""
        return self._data.get(sid)

    def set_session(self, sid: str, data: dict[str, Any]) -> None:
        """Store a session dict."""
        self._data[sid] = data

    def delete_session(self, sid: str) -> None:
        """Delete a session.  No-op if missing."""
        self._data.pop(sid, None)

    def has_session(self, sid: str) -> bool:
        """Check whether a session exists."""
        return sid in self._data

    def all_sessions(self) -> list[tuple[str, dict[str, Any]]]:
        """Return a snapshot list of ``(sid, session)`` pairs."""
        return list(self._data.items())

    def session_count(self) -> int:
        """Return the number of active sessions."""
        return len(self._data)

    def all_sids(self) -> list[str]:
        """Return a list of all active session IDs."""
        return list(self._data.keys())

    # -- Expired tracking --------------------------------------------------

    def mark_expired(self, sid: str, timestamp: float) -> None:
        """Record a SID as expired."""
        self._expired.add((sid, timestamp))

    def is_expired(self, sid: str) -> bool:
        """Check whether a SID was recently expired."""
        return any(s == sid for s, _ in self._expired)

    def clear_expired(self, sid: str) -> None:
        """Remove a SID from the expired set."""
        self._expired.difference_update({(s, t) for s, t in self._expired if s == sid})

    def prune_expired(self, max_age: float) -> None:
        """Remove expired records older than *max_age* seconds."""
        now = time.time()
        self._expired.difference_update(
            {(s, t) for s, t in self._expired if now - t > max_age}
        )

    def expired_count(self) -> int:
        """Return the number of tracked expired SIDs."""
        return len(self._expired)
