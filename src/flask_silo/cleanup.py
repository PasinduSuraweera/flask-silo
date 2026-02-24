"""Daemon thread for periodic session cleanup.

Wraps :meth:`SessionStore.cleanup <flask_silo.store.SessionStore.cleanup>`
in a stoppable daemon thread that runs at a configurable interval.

The thread is marked as a *daemon*, so it dies automatically when the
main process exits - no explicit teardown is required in production,
though :meth:`CleanupDaemon.stop` is available for graceful shutdown
and test teardown.
"""

from __future__ import annotations

import threading
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import SessionStore

logger = logging.getLogger("flask_silo.cleanup")


class CleanupDaemon:
    """Background daemon that periodically purges expired sessions.

    Uses :class:`threading.Event` for interruptible sleep, allowing the
    daemon to be stopped promptly without waiting for the full interval.

    Parameters
    ----------
    store:
        The :class:`~flask_silo.store.SessionStore` to clean.
    interval:
        Seconds between cleanup passes.  Defaults to
        ``store.cleanup_interval``.

    Example::

        daemon = CleanupDaemon(store, interval=60)
        daemon.start()
        # ...
        daemon.stop()   # optional - daemon thread dies with the process
    """

    __slots__ = ("_store", "_interval", "_thread", "_stop_event")

    def __init__(
        self, store: SessionStore, interval: int | None = None
    ) -> None:
        self._store = store
        self._interval = interval if interval is not None else store.cleanup_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Internal loop ──────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Sleep → cleanup → repeat, until ``_stop_event`` is set."""
        while not self._stop_event.wait(timeout=self._interval):
            try:
                expired = self._store.cleanup()
                if expired:
                    logger.info(
                        "Cleaned up %d expired session(s): %s",
                        len(expired),
                        expired,
                    )
            except Exception:
                logger.exception("Error during session cleanup")

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the cleanup daemon thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="flask-silo-cleanup",
            daemon=True,
        )
        self._thread.start()
        logger.debug("Cleanup daemon started (interval=%ds)", self._interval)

    def stop(self) -> None:
        """Signal the daemon to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None
        logger.debug("Cleanup daemon stopped")

    @property
    def running(self) -> bool:
        """Whether the daemon thread is currently alive."""
        return self._thread is not None and self._thread.is_alive()
