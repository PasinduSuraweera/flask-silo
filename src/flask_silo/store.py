"""Thread-safe, TTL-enforced session state manager.

This is the core of Flask-Silo.  Each session is identified by a string SID
and contains one or more *namespaces*, each initialised by a registered
factory function.  The store handles creation, access, TTL-based cleanup,
expired-SID tracking (for the 410 Gone pattern), and lifecycle callbacks.

The underlying data layer is pluggable via :class:`~flask_silo.storage.SiloStorage`.
By default an :class:`~flask_silo.storage.InMemoryStorage` backend is used.
Thread-safety is guaranteed through a process-local lock that coordinates
all access to the storage backend.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from .errors import NamespaceError, SessionBusy
from .storage import InMemoryStorage, SiloStorage


class SessionStore:
    """Thread-safe session state manager with TTL enforcement.

    Features
    --------
    * **Pluggable storage** – defaults to :class:`InMemoryStorage` but
      accepts any :class:`SiloStorage` backend (e.g. Redis).
    * **Namespace isolation** – register multiple independent state dicts per
      session via :meth:`register_namespace`.
    * **TTL cleanup** - :meth:`cleanup` removes sessions whose
      ``last_active`` exceeded *ttl* seconds ago.
    * **Expired-SID tracking** - recently-cleaned-up SIDs are remembered for
      *expired_retain* seconds so the server can reply 410 Gone instead of
      silently creating new sessions.
    * **Busy-check predicate** - a user-supplied callable can veto cleanup
      of sessions that are "busy" (e.g. running a background task).
    * **Lifecycle callbacks** - ``on_create`` / ``on_expire`` hooks.

    Example::

        store = SessionStore(ttl=3600)
        store.register_namespace('cart', lambda: {'items': [], 'total': 0.0})

        session = store.get('user-abc123')
        session['cart']['items'].append({'sku': 'A1', 'qty': 2})

    Parameters
    ----------
    ttl:
        Session time-to-live in seconds (default 3 600 = 1 hour).
    cleanup_interval:
        Suggested interval for periodic cleanup (used by
        :class:`~flask_silo.cleanup.CleanupDaemon`).
    expired_retain:
        How long to remember expired SIDs (default 7 200 = 2 hours).
    sid_generator:
        Custom callable returning a new SID string.  Defaults to
        ``uuid.uuid4().hex``.
    storage:
        Storage backend instance.  Defaults to :class:`InMemoryStorage`.
    """

    __slots__ = (
        "storage",
        "_lock",
        "_factories",
        "_busy_check",
        "_on_expire_cbs",
        "_on_create_cbs",
        "ttl",
        "cleanup_interval",
        "expired_retain",
        "_sid_gen",
    )

    def __init__(
        self,
        *,
        ttl: int = 3600,
        cleanup_interval: int = 60,
        expired_retain: int = 7200,
        sid_generator: Callable[[], str] | None = None,
        storage: SiloStorage | None = None,
    ) -> None:
        self.storage: SiloStorage = storage or InMemoryStorage()
        self._lock = threading.Lock()
        self._factories: dict[str, Callable[[], dict[str, Any]]] = {}
        self._busy_check: Callable[[str, dict[str, Any]], bool] | None = None
        self._on_expire_cbs: list[Callable[[str], None]] = []
        self._on_create_cbs: list[Callable[[str], None]] = []
        self.ttl = ttl
        self.cleanup_interval = cleanup_interval
        self.expired_retain = expired_retain
        self._sid_gen = sid_generator or (lambda: uuid.uuid4().hex)

    # ── Namespace registration ─────────────────────────────────────────────

    def register_namespace(
        self, name: str, factory: Callable[[], dict[str, Any]]
    ) -> None:
        """Register a state namespace with a factory function.

        The factory is called to create default state whenever a new session
        is created, an existing session is reset, or a session created before
        this namespace was registered is accessed (lazy initialisation).

        Args:
            name: Namespace identifier (e.g. ``'cart'``, ``'uploads'``).
            factory: Callable returning a fresh default-state dict.

        Example::

            store.register_namespace(
                'cart', lambda: {'items': [], 'total': 0.0}
            )
        """
        self._factories[name] = factory

    # ── Session creation (internal) ────────────────────────────────────────

    def _create_session(self, sid: str) -> dict[str, Any]:
        """Build a fresh session dict with all registered namespaces."""
        session: dict[str, Any] = {
            ns: factory() for ns, factory in self._factories.items()
        }
        session["_meta"] = {
            "created_at": time.time(),
            "last_active": time.time(),
            "sid": sid,
        }
        return session

    # ── Public API ─────────────────────────────────────────────────────────

    def get(self, sid: str) -> dict[str, Any]:
        """Get or create session state.

        * If the SID does not exist, a new session is created with all
          registered namespaces and the ``on_create`` callbacks fire.
        * If the SID was previously marked as expired (e.g. after TTL
          cleanup), it is removed from the expired tracker - this enables
          the "re-upload after expiry" pattern.
        * Any namespaces registered *after* this session was created are
          lazily initialised.

        Args:
            sid: Session identifier string.

        Returns:
            The full session dict (namespace keys + ``_meta``).
        """
        with self._lock:
            session = self.storage.get_session(sid)
            is_new = session is None
            if is_new:
                session = self._create_session(sid)
                # Clear from expired tracker - user is re-uploading
                self.storage.clear_expired(sid)
            assert session is not None  # guaranteed by branch above
            # Lazy-init namespaces added after session creation
            for ns, factory in self._factories.items():
                if ns not in session:
                    session[ns] = factory()
            session["_meta"]["last_active"] = time.time()
            self.storage.set_session(sid, session)

        # Fire create callbacks outside the main lock
        if is_new:
            for cb in self._on_create_cbs:
                with contextlib.suppress(Exception):
                    cb(sid)

        return session

    def get_namespace(self, sid: str, namespace: str) -> dict[str, Any]:
        """Get state for a specific namespace.

        Args:
            sid: Session identifier.
            namespace: Registered namespace name.

        Returns:
            The namespace's state dict.

        Raises:
            NamespaceError: If the namespace is not registered.
        """
        if namespace not in self._factories:
            raise NamespaceError(namespace)
        return self.get(sid)[namespace]  # type: ignore[no-any-return]

    def touch(self, sid: str) -> None:
        """Update ``last_active`` without creating a new session."""
        with self._lock:
            session = self.storage.get_session(sid)
            if session is not None:
                session["_meta"]["last_active"] = time.time()
                self.storage.set_session(sid, session)

    def exists(self, sid: str) -> bool:
        """Check whether a session is active (not expired)."""
        with self._lock:
            return self.storage.has_session(sid)

    def is_expired(self, sid: str) -> bool:
        """Check whether a SID was recently expired due to TTL.

        This supports the **410 Gone** pattern: when a client returns after
        their session was cleaned up, the server can detect this and respond
        with ``410`` instead of silently creating a new empty session.
        """
        with self._lock:
            return self.storage.is_expired(sid)

    # ── Cleanup ────────────────────────────────────────────────────────────

    def cleanup(self) -> list[str]:
        """Remove stale sessions and prune old expired-SID records.

        A session is **stale** if:

        1. Its ``last_active`` time exceeds :attr:`ttl` seconds ago, **AND**
        2. The :meth:`set_busy_check` predicate (if set) returns ``False``.

        Expired SID records older than :attr:`expired_retain` are pruned.

        Returns:
            List of session IDs that were expired in this pass.
        """
        now = time.time()
        expired_sids: list[str] = []

        with self._lock:
            for sid, session in self.storage.all_sessions():
                age = now - session["_meta"]["last_active"]
                if age > self.ttl:
                    if self._busy_check and self._busy_check(sid, session):
                        continue  # skip busy sessions
                    self.storage.delete_session(sid)
                    self.storage.mark_expired(sid, now)
                    expired_sids.append(sid)

        # Fire expiry callbacks
        for sid in expired_sids:
            for cb in self._on_expire_cbs:
                with contextlib.suppress(Exception):
                    cb(sid)

        # Prune old entries from the expired tracker
        with self._lock:
            self.storage.prune_expired(self.expired_retain)

        return expired_sids

    # ── Reset / destroy ────────────────────────────────────────────────────

    def reset(self, sid: str) -> None:
        """Reset a session to fresh default state.

        Replaces all namespace state with fresh factory output.

        Args:
            sid: Session identifier.

        Raises:
            SessionBusy: If the busy-check predicate returns ``True``.
        """
        with self._lock:
            session = self.storage.get_session(sid)
            if session is not None:
                if self._busy_check and self._busy_check(sid, session):
                    raise SessionBusy(sid)
                self.storage.set_session(sid, self._create_session(sid))

    def destroy(self, sid: str) -> None:
        """Completely remove a session **without** tracking it as expired."""
        with self._lock:
            self.storage.delete_session(sid)

    # ── Configuration ──────────────────────────────────────────────────────

    def set_busy_check(self, predicate: Callable[[str, dict[str, Any]], bool]) -> None:
        """Set a predicate controlling whether a session can be cleaned up.

        The predicate receives ``(sid, session_dict)`` and should return
        ``True`` if the session is "busy" and must **not** be expired.

        Args:
            predicate: ``(sid, session) -> bool``

        Example::

            store.set_busy_check(
                lambda sid, s: s['processing']['task'].is_running
            )
        """
        self._busy_check = predicate

    def on_expire(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked when a session expires.

        The callback receives the expired session ID.  Multiple callbacks
        can be registered; they run in registration order.
        """
        self._on_expire_cbs.append(callback)

    def on_create(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked when a new session is created."""
        self._on_create_cbs.append(callback)

    # ── SID generation ─────────────────────────────────────────────────────

    def generate_sid(self) -> str:
        """Generate a new session ID using the configured generator."""
        return self._sid_gen()

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        """Number of currently active sessions."""
        with self._lock:
            return self.storage.session_count()

    @property
    def expired_count(self) -> int:
        """Number of tracked expired session IDs."""
        with self._lock:
            return self.storage.expired_count()

    @property
    def all_sids(self) -> list[str]:
        """List of all active session IDs (snapshot)."""
        with self._lock:
            return self.storage.all_sids()

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, sid: str, session: dict[str, Any]) -> None:
        """Persist session changes to the storage backend.

        Called automatically by :class:`~flask_silo.ext.Silo` in the
        ``after_request`` hook.  For :class:`InMemoryStorage` this is
        effectively a no-op (the dict is already a live reference); for
        external backends (Redis, etc.) this writes the modified session
        back to the data store.

        Args:
            sid: Session identifier.
            session: The session dict to persist.
        """
        with self._lock:
            self.storage.set_session(sid, session)
