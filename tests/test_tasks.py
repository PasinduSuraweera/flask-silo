"""Tests for BackgroundTask progress tracking.

Covers: start/complete, auto-complete, failure handling, progress clamping,
reset, args/kwargs forwarding, timestamps, and concurrency guards.
"""

import time
import threading

import pytest
from flask_silo import BackgroundTask, TaskState


# ── TaskState unit tests ───────────────────────────────────────────────────


class TestTaskState:
    def test_defaults(self):
        ts = TaskState()
        assert not ts.running
        assert ts.progress == 0.0
        assert ts.logs == []
        assert ts.error is None
        assert ts.started_at is None
        assert ts.finished_at is None

    def test_to_dict(self):
        ts = TaskState(running=True, progress=50.0, message="halfway")
        d = ts.to_dict()
        assert d["running"] is True
        assert d["progress"] == 50.0
        assert d["message"] == "halfway"

    def test_to_dict_truncates_logs(self):
        ts = TaskState(logs=[f"log-{i}" for i in range(100)])
        d = ts.to_dict(max_logs=10)
        assert len(d["logs"]) == 10
        assert d["logs"][0] == "log-90"  # last 10


# ── BackgroundTask lifecycle tests ─────────────────────────────────────────


class TestBackgroundTask:
    def test_start_and_explicit_complete(self):
        def work(task):
            task.update(progress=50, message="working")
            task.log("did stuff")
            task.complete("all done")

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)

        assert bg.is_complete
        assert not bg.is_running
        state = bg.state
        assert state.progress == 100.0
        assert state.message == "all done"
        assert state.logs == ["did stuff"]

    def test_auto_complete_on_return(self):
        def work(task):
            task.update(progress=100)

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)

        assert bg.is_complete
        assert not bg.is_running

    def test_exception_marks_failure(self):
        def work(task):
            raise ValueError("something broke")

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)

        assert bg.is_failed
        assert not bg.is_running
        assert "something broke" in bg.state.error

    def test_explicit_fail(self):
        def work(task):
            task.fail("manual error")

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)

        assert bg.is_failed
        assert bg.state.error == "manual error"

    def test_cannot_start_twice(self):
        event = threading.Event()

        def work(task):
            event.wait()

        bg = BackgroundTask("test")
        bg.start(work)
        with pytest.raises(RuntimeError, match="already running"):
            bg.start(work)
        event.set()
        time.sleep(0.1)

    def test_reset_and_rerun(self):
        def work(task):
            task.complete("done")

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)
        assert bg.is_complete

        bg.reset()
        assert not bg.is_complete
        assert not bg.is_running

        bg.start(work)
        time.sleep(0.5)
        assert bg.is_complete

    def test_cannot_reset_while_running(self):
        event = threading.Event()

        def work(task):
            event.wait()

        bg = BackgroundTask("test")
        bg.start(work)
        with pytest.raises(RuntimeError, match="Cannot reset"):
            bg.reset()
        event.set()
        time.sleep(0.1)

    def test_progress_clamped_to_range(self):
        bg = BackgroundTask("test")
        bg._state.running = True
        bg.update(progress=150)
        assert bg.state.progress == 100.0
        bg.update(progress=-10)
        assert bg.state.progress == 0.0

    def test_args_and_kwargs_forwarded(self):
        results = {}

        def work(task, x, y, z=None):
            results["x"] = x
            results["y"] = y
            results["z"] = z

        bg = BackgroundTask("test")
        bg.start(work, 1, 2, z=3)
        time.sleep(0.5)
        assert results == {"x": 1, "y": 2, "z": 3}

    def test_timestamps_set(self):
        def work(task):
            time.sleep(0.05)

        bg = BackgroundTask("test")
        before = time.time()
        bg.start(work)
        time.sleep(0.5)
        after = time.time()

        state = bg.state
        assert state.started_at is not None
        assert state.finished_at is not None
        assert before <= state.started_at <= after
        assert state.started_at <= state.finished_at <= after

    def test_state_is_a_copy(self):
        def work(task):
            task.log("entry")
            task.complete("done")

        bg = BackgroundTask("test")
        bg.start(work)
        time.sleep(0.5)

        snap1 = bg.state
        snap2 = bg.state
        assert snap1.logs is not snap2.logs  # independent copies
