"""Thread-based background task manager with progress tracking.

Provides :class:`BackgroundTask`, a self-contained wrapper that runs a
callable in a daemon thread and exposes thread-safe methods to report
progress, append log entries, and signal completion or failure.

The companion :class:`TaskState` dataclass is the serialisable snapshot
returned by :attr:`BackgroundTask.state`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("flask_silo.tasks")


# ── Task state snapshot ────────────────────────────────────────────────────


@dataclass
class TaskState:
    """Immutable snapshot of a background task's current state.

    Attributes:
        running: Whether the task is currently executing.
        progress: Completion percentage (0.0 – 100.0).
        message: Human-readable status message.
        logs: Chronological log entries.
        complete: Whether the task finished successfully.
        error: Error message if the task failed, else ``None``.
        started_at: Unix timestamp when the task started.
        finished_at: Unix timestamp when the task finished.
    """

    running: bool = False
    progress: float = 0.0
    message: str = ""
    logs: list[str] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self, *, max_logs: int = 50) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict.

        Args:
            max_logs: Maximum number of recent log entries to include.
        """
        return {
            "running": self.running,
            "progress": round(self.progress, 1),
            "message": self.message,
            "logs": self.logs[-max_logs:],
            "complete": self.complete,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── Background task runner ─────────────────────────────────────────────────


class BackgroundTask:
    """Manages a background thread with progress reporting.

    The target function receives the ``BackgroundTask`` instance as its
    first argument, followed by any positional/keyword arguments passed
    to :meth:`start`::

        def classify(task: BackgroundTask, filepath: str, api_key: str):
            for i, batch in enumerate(batches):
                process(batch)
                task.update(
                    progress=(i + 1) / len(batches) * 100,
                    message=f"Batch {i + 1}/{len(batches)}",
                )
                task.log(f"Processed batch {i + 1}")
            task.complete("Classification finished")

        bg = BackgroundTask("classify")
        bg.start(classify, "/data/file.xlsx", api_key="sk-xxx")

    If the target returns without calling :meth:`complete` or :meth:`fail`,
    the task is automatically marked as complete.

    Parameters
    ----------
    name:
        Human-readable task name (used in thread name and logs).
    """

    __slots__ = ("name", "_state", "_lock", "_thread")

    def __init__(self, name: str = "task") -> None:
        self.name = name
        self._state = TaskState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self, target: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Start the task in a daemon thread.

        Args:
            target: The function to execute.  Receives this
                    ``BackgroundTask`` as its first argument.
            *args: Additional positional arguments for the target.
            **kwargs: Additional keyword arguments for the target.

        Raises:
            RuntimeError: If a task is already running.
        """
        with self._lock:
            if self._state.running:
                raise RuntimeError(f"Task '{self.name}' is already running")
            self._state = TaskState(
                running=True,
                message="Starting…",
                started_at=time.time(),
            )

        def _wrapper() -> None:
            try:
                target(self, *args, **kwargs)
                # Auto-complete if target didn't explicitly call complete/fail
                with self._lock:
                    if self._state.running:
                        self._state.running = False
                        self._state.complete = True
                        self._state.progress = 100.0
                        self._state.finished_at = time.time()
            except Exception as exc:
                self.fail(str(exc))
                logger.exception("Task '%s' failed", self.name)

        self._thread = threading.Thread(
            target=_wrapper,
            name=f"flask-silo-task-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def reset(self) -> None:
        """Reset task state so it can be re-run.

        Raises:
            RuntimeError: If the task is currently running.
        """
        with self._lock:
            if self._state.running:
                raise RuntimeError(f"Cannot reset running task '{self.name}'")
            self._state = TaskState()

    # ── Progress reporting (called by the target function) ─────────────────

    def update(
        self,
        progress: float | None = None,
        message: str | None = None,
    ) -> None:
        """Update task progress (thread-safe).

        Args:
            progress: Completion percentage, clamped to [0, 100].
            message: Human-readable status message.
        """
        with self._lock:
            if progress is not None:
                self._state.progress = min(max(progress, 0.0), 100.0)
            if message is not None:
                self._state.message = message

    def log(self, message: str) -> None:
        """Append a log entry (thread-safe)."""
        with self._lock:
            self._state.logs.append(message)

    def complete(self, message: str = "Done") -> None:
        """Mark the task as successfully completed."""
        with self._lock:
            self._state.running = False
            self._state.complete = True
            self._state.progress = 100.0
            self._state.message = message
            self._state.finished_at = time.time()

    def fail(self, error: str) -> None:
        """Mark the task as failed."""
        with self._lock:
            self._state.running = False
            self._state.error = error
            self._state.message = f"Failed: {error}"
            self._state.finished_at = time.time()

    # ── State access ───────────────────────────────────────────────────────

    @property
    def state(self) -> TaskState:
        """Snapshot of the current task state (thread-safe copy)."""
        with self._lock:
            return TaskState(
                running=self._state.running,
                progress=self._state.progress,
                message=self._state.message,
                logs=list(self._state.logs),
                complete=self._state.complete,
                error=self._state.error,
                started_at=self._state.started_at,
                finished_at=self._state.finished_at,
            )

    @property
    def is_running(self) -> bool:
        """Whether the task is currently executing."""
        with self._lock:
            return self._state.running

    @property
    def is_complete(self) -> bool:
        """Whether the task finished successfully."""
        with self._lock:
            return self._state.complete

    @property
    def is_failed(self) -> bool:
        """Whether the task failed with an error."""
        with self._lock:
            return self._state.error is not None
