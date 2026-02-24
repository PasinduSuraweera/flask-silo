"""Flask extension integrating session-isolated state management.

:class:`Silo` is the main entry point for most users.  It wires together
a :class:`~flask_silo.store.SessionStore`,
:class:`~flask_silo.cleanup.CleanupDaemon`, and any number of
:class:`~flask_silo.files.FileStore` instances into Flask's request
lifecycle via ``before_request`` / ``after_request`` hooks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask, g, jsonify, request

from .cleanup import CleanupDaemon
from .files import FileStore
from .store import SessionStore

logger = logging.getLogger("flask_silo")


class Silo:
    """Flask extension for session-isolated state management.

    Provides per-client state isolation using a **header-based session ID**.
    Each client gets independent state namespaces, automatic TTL-based
    cleanup, and configurable file storage.

    Supports both direct initialisation and the Flask **factory pattern**::

        # Direct
        app = Flask(__name__)
        silo = Silo(app, ttl=3600)

        # Factory pattern
        silo = Silo(ttl=3600)
        silo.init_app(app)

    Session state is accessible via :py:data:`flask.g`::

        @app.route('/api/data')
        def get_data():
            data = g.silo['processing']  # or silo.state('processing')
            return jsonify(data)

    Parameters
    ----------
    app:
        Flask application (optional - use :meth:`init_app` for factories).
    ttl:
        Session time-to-live in seconds (default 3 600).
    cleanup_interval:
        Seconds between cleanup passes (default 60).
    expired_retain:
        Seconds to remember expired SIDs for 410 responses (default 7 200).
    header:
        HTTP header for the session ID (default ``X-Session-ID``).
    query_param:
        Query-string fallback for the session ID (default ``_sid``).
    min_sid_length:
        Minimum length to accept a client-supplied SID (default 16).
    data_endpoints:
        URL paths that require existing data - expired sessions hitting
        these get a 410 response.
    auto_cleanup:
        Start the cleanup daemon automatically (default ``True``).
    api_prefix:
        URL prefix that triggers session handling (default ``/api/``).
    """

    def __init__(
        self,
        app: Flask | None = None,
        *,
        ttl: int = 3600,
        cleanup_interval: int = 60,
        expired_retain: int = 7200,
        header: str = "X-Session-ID",
        query_param: str = "_sid",
        min_sid_length: int = 16,
        data_endpoints: set[str] | None = None,
        auto_cleanup: bool = True,
        api_prefix: str = "/api/",
    ) -> None:
        self._header = header
        self._query_param = query_param
        self._min_sid_length = min_sid_length
        self._data_endpoints: set[str] = set(data_endpoints or ())
        self._auto_cleanup = auto_cleanup
        self._api_prefix = api_prefix
        self._file_stores: dict[str, FileStore] = {}

        self.store = SessionStore(
            ttl=ttl,
            cleanup_interval=cleanup_interval,
            expired_retain=expired_retain,
        )
        self._daemon = CleanupDaemon(self.store, interval=cleanup_interval)

        # Auto-clean file stores when sessions expire
        self.store.on_expire(self._on_session_expire)

        if app is not None:
            self.init_app(app)

    # ── Flask integration ──────────────────────────────────────────────────

    def init_app(self, app: Flask) -> None:
        """Initialise with a Flask application.

        Registers ``before_request`` and ``after_request`` hooks and
        (optionally) starts the cleanup daemon.
        """
        app.before_request(self._before_request)
        app.after_request(self._after_request)

        # Store reference on the app for other extensions
        app.extensions["silo"] = self

        if self._auto_cleanup:
            self._daemon.start()

        logger.info(
            "Flask-Silo initialised (ttl=%ds, cleanup=%ds, auto_cleanup=%s)",
            self.store.ttl,
            self.store.cleanup_interval,
            self._auto_cleanup,
        )

    # ── Namespace & file-store registration ────────────────────────────────

    def register(self, namespace: str, factory: Callable[[], dict[str, Any]]) -> None:
        """Register a state namespace with a default factory.

        Must be called **before** the first request.

        Args:
            namespace: Unique name for the namespace.
            factory: Callable returning the default state dict.

        Example::

            silo.register('processing', lambda: {
                'data': None,
                'task': BackgroundTask('process'),
            })
        """
        self.store.register_namespace(namespace, factory)

    def add_file_store(self, name: str, base_dir: str) -> FileStore:
        """Register a per-session file store.

        Files are automatically cleaned up when sessions expire.

        Args:
            name: Identifier for this file store.
            base_dir: Root directory for session file storage.

        Returns:
            The created :class:`~flask_silo.files.FileStore` instance.
        """
        fs = FileStore(base_dir)
        self._file_stores[name] = fs
        return fs

    def file_store(self, name: str) -> FileStore:
        """Get a registered file store by name.

        Args:
            name: The identifier passed to :meth:`add_file_store`.

        Raises:
            KeyError: If no file store with that name exists.
        """
        return self._file_stores[name]

    def add_data_endpoints(self, *paths: str) -> None:
        """Add URL paths that should return 410 for expired sessions.

        These are endpoints that **require existing session data**.
        If an expired client hits one of these, the server responds with
        ``410 Gone`` instead of silently creating a new empty session.

        Args:
            *paths: URL path strings (e.g. ``'/api/report'``).
        """
        self._data_endpoints.update(paths)

    # ── Request lifecycle hooks ────────────────────────────────────────────

    def _extract_sid(self) -> str:
        """Extract session ID from request header or query param."""
        sid = request.headers.get(self._header, "").strip()
        if not sid or len(sid) < self._min_sid_length:
            sid = request.args.get(self._query_param, "").strip()
        if not sid or len(sid) < self._min_sid_length:
            sid = self.store.generate_sid()
        return sid

    def _before_request(self) -> Any:
        """Inject session state into ``g`` before each API request."""
        if not request.path.startswith(self._api_prefix):
            return None

        sid = self._extract_sid()

        # 410 Gone for expired sessions on data-dependent endpoints
        if self.store.is_expired(sid) and request.path in self._data_endpoints:
            return (
                jsonify(
                    {
                        "error": "session_expired",
                        "message": (
                            "Your session has expired due to inactivity. "
                            "Please re-upload your data."
                        ),
                    }
                ),
                410,
            )

        session = self.store.get(sid)
        g.silo_sid = sid
        g.silo = session
        return None

    def _after_request(self, response: Any) -> Any:
        """Add the session ID to response headers."""
        sid = getattr(g, "silo_sid", None)
        if sid:
            response.headers[self._header] = sid
        return response

    def _on_session_expire(self, sid: str) -> None:
        """Clean up file stores when a session expires."""
        for fs in self._file_stores.values():
            try:
                fs.cleanup(sid)
            except Exception:
                logger.exception("Error cleaning up file store for session %s", sid)

    # ── Convenience accessors ──────────────────────────────────────────────

    def state(self, namespace: str | None = None) -> dict[str, Any]:
        """Get session state for the **current request**.

        Args:
            namespace: If provided, return only that namespace's state.
                       If ``None``, return the full session dict.

        Returns:
            Session state dict.

        Raises:
            RuntimeError: If called outside a request context.
        """
        session = getattr(g, "silo", None)
        if session is None:
            raise RuntimeError("No active session. Are you inside a request context?")
        if namespace:
            return session[namespace]  # type: ignore[no-any-return]
        return session  # type: ignore[no-any-return]

    @property
    def sid(self) -> str:
        """Current request's session ID.

        Raises:
            RuntimeError: If called outside a request context.
        """
        sid: str | None = getattr(g, "silo_sid", None)
        if sid is None:
            raise RuntimeError("No active session. Are you inside a request context?")
        return sid

    def reset_current(self) -> None:
        """Reset the current session to fresh state.

        Also cleans up all registered file stores for this session.

        Raises:
            SessionBusy: If the busy-check predicate returns ``True``.
        """
        sid = self.sid
        self.store.reset(sid)
        for fs in self._file_stores.values():
            fs.cleanup(sid)
        # Refresh g.silo with the new state
        g.silo = self.store.get(sid)

    def stop(self) -> None:
        """Stop the cleanup daemon gracefully."""
        self._daemon.stop()
