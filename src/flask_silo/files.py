"""Per-session file storage management.

Provides :class:`FileStore`, which maps each session ID to its own
subdirectory under a configurable base path.  Files are isolated between
sessions and can be cleaned up individually or in bulk.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from typing import BinaryIO

logger = logging.getLogger("flask_silo.files")


class FileStore:
    """Manages per-session file directories with automatic lifecycle.

    Each session gets its own subdirectory under *base_dir*.  When a
    session expires, call :meth:`cleanup` to remove its files - or let
    :class:`~flask_silo.ext.Silo` do it automatically via the
    ``on_expire`` callback.

    Parameters
    ----------
    base_dir:
        Root directory for all session file storage.
    auto_create:
        Create directories automatically on access (default ``True``).

    Example::

        files = FileStore("/tmp/uploads")
        path = files.save("session-123", "report.xlsx", file_obj)
        files.cleanup("session-123")  # removes everything
    """

    __slots__ = ("base_dir", "auto_create")

    def __init__(self, base_dir: str, *, auto_create: bool = True) -> None:
        self.base_dir = os.path.abspath(base_dir)
        self.auto_create = auto_create
        if auto_create:
            os.makedirs(self.base_dir, exist_ok=True)

    # ── Directory access ───────────────────────────────────────────────────

    def session_dir(self, sid: str) -> str:
        """Get the directory path for a session, creating it if needed.

        Args:
            sid: Session identifier (used as the directory name).

        Returns:
            Absolute path to the session's directory.
        """
        d = os.path.join(self.base_dir, sid)
        if self.auto_create:
            os.makedirs(d, exist_ok=True)
        return d

    # ── File operations ────────────────────────────────────────────────────

    def save(self, sid: str, filename: str, data: BinaryIO | bytes) -> str:
        """Save a file into the session's directory.

        Args:
            sid: Session identifier.
            filename: Name for the saved file.
            data: File-like object (with ``.read()``) or raw ``bytes``.

        Returns:
            Absolute path to the saved file.
        """
        d = self.session_dir(sid)
        path = os.path.join(d, filename)
        if isinstance(data, bytes):
            with open(path, "wb") as f:
                f.write(data)
        else:
            with open(path, "wb") as f:
                while True:
                    chunk = data.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        logger.debug("Saved file: %s", path)
        return path

    def get_path(self, sid: str, filename: str) -> str | None:
        """Get path to an existing file, or ``None`` if it doesn't exist.

        Args:
            sid: Session identifier.
            filename: Name of the file.

        Returns:
            Absolute path if the file exists, ``None`` otherwise.
        """
        path = os.path.join(self.base_dir, sid, filename)
        return path if os.path.isfile(path) else None

    def list_files(self, sid: str) -> list[str]:
        """List filenames in a session's directory.

        Args:
            sid: Session identifier.

        Returns:
            List of filenames (not full paths).  Empty list if the
            directory does not exist.
        """
        d = os.path.join(self.base_dir, sid)
        if not os.path.isdir(d):
            return []
        return os.listdir(d)

    # ── Cleanup ────────────────────────────────────────────────────────────

    def cleanup(self, sid: str) -> None:
        """Remove all files and the directory for a session.

        Args:
            sid: Session identifier.
        """
        d = os.path.join(self.base_dir, sid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            logger.debug("Cleaned up session dir: %s", d)

    def cleanup_all(self) -> None:
        """Remove all session directories under *base_dir*."""
        if os.path.isdir(self.base_dir):
            for name in os.listdir(self.base_dir):
                d = os.path.join(self.base_dir, name)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            logger.info("Cleaned up all session directories in %s", self.base_dir)

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def total_size_bytes(self) -> int:
        """Total disk usage across all session directories."""
        total = 0
        for dirpath, _, filenames in os.walk(self.base_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                with contextlib.suppress(OSError):
                    total += os.path.getsize(fp)
        return total
