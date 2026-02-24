"""Tests for session TTL enforcement and the CleanupDaemon.

Covers: stale removal, active retention, expired-SID tracking/pruning,
re-upload clearing, busy-check veto, daemon start/stop, and real-time TTL.
"""

import time

import pytest

from flask_silo import CleanupDaemon, SessionStore


@pytest.fixture
def store():
    s = SessionStore(ttl=2, cleanup_interval=1, expired_retain=5)
    s.register_namespace("data", lambda: {"value": None})
    return s


# ── Cleanup function unit tests ───────────────────────────────────────────


class TestCleanup:
    def test_stale_session_removed(self, store):
        store.get("sid-001")
        store._sessions["sid-001"]["_meta"]["last_active"] = time.time() - 10
        expired = store.cleanup()
        assert "sid-001" in expired
        assert not store.exists("sid-001")

    def test_active_session_kept(self, store):
        store.get("sid-001")
        expired = store.cleanup()
        assert expired == []
        assert store.exists("sid-001")

    def test_expired_sid_tracked(self, store):
        store.get("sid-001")
        store._sessions["sid-001"]["_meta"]["last_active"] = time.time() - 10
        store.cleanup()
        assert store.is_expired("sid-001")

    def test_expired_sid_pruned_after_retain(self, store):
        store._expired.add(("old-sid", time.time() - store.expired_retain - 1))
        store.cleanup()
        assert not store.is_expired("old-sid")

    def test_reupload_clears_expired(self, store):
        store.get("sid-001")
        store._sessions["sid-001"]["_meta"]["last_active"] = time.time() - 10
        store.cleanup()
        assert store.is_expired("sid-001")
        store.get("sid-001")  # re-create
        assert not store.is_expired("sid-001")

    def test_busy_check_prevents_cleanup(self, store):
        store.set_busy_check(lambda sid, s: s["data"]["value"] == "busy")
        store.get("sid-001")["data"]["value"] = "busy"
        store._sessions["sid-001"]["_meta"]["last_active"] = time.time() - 10
        expired = store.cleanup()
        assert expired == []
        assert store.exists("sid-001")

    def test_expired_count(self, store):
        for i in range(3):
            sid = f"sid-{i:03d}"
            store.get(sid)
            store._sessions[sid]["_meta"]["last_active"] = time.time() - 10
        store.cleanup()
        assert store.expired_count == 3

    def test_cleanup_multiple_stale(self, store):
        for i in range(5):
            sid = f"sid-{i:03d}"
            store.get(sid)
            store._sessions[sid]["_meta"]["last_active"] = time.time() - 10
        expired = store.cleanup()
        assert len(expired) == 5
        assert store.active_count == 0


# ── CleanupDaemon tests ───────────────────────────────────────────────────


class TestCleanupDaemon:
    def test_starts_and_stops(self, store):
        daemon = CleanupDaemon(store, interval=1)
        daemon.start()
        assert daemon.running
        daemon.stop()
        assert not daemon.running

    def test_start_is_idempotent(self, store):
        daemon = CleanupDaemon(store, interval=1)
        daemon.start()
        daemon.start()  # should not raise or create extra threads
        daemon.stop()

    def test_daemon_cleans_stale_sessions(self, store):
        store.get("sid-001")
        store._sessions["sid-001"]["_meta"]["last_active"] = time.time() - 10
        daemon = CleanupDaemon(store, interval=0.3)
        daemon.start()
        time.sleep(1)
        daemon.stop()
        assert not store.exists("sid-001")
        assert store.is_expired("sid-001")


# ── Real-time TTL tests ───────────────────────────────────────────────────


class TestRealTimeTTL:
    def test_session_expires_after_ttl(self, store):
        """Session should be cleaned up after TTL elapses."""
        store.get("sid-001")
        daemon = CleanupDaemon(store, interval=0.3)
        daemon.start()
        time.sleep(3)
        daemon.stop()
        assert not store.exists("sid-001")
        assert store.is_expired("sid-001")

    def test_activity_resets_ttl(self, store):
        """Touching a session should reset its TTL clock."""
        store.get("sid-001")
        daemon = CleanupDaemon(store, interval=0.3)
        daemon.start()
        time.sleep(1)
        store.touch("sid-001")
        time.sleep(1)
        assert store.exists("sid-001")  # still alive
        daemon.stop()

    def test_full_lifecycle(self, store):
        """Create → expire → 410 check → re-create → no longer expired."""
        store.get("sid-001")
        daemon = CleanupDaemon(store, interval=0.3)
        daemon.start()
        time.sleep(3)
        assert store.is_expired("sid-001")
        store.get("sid-001")  # re-create
        assert not store.is_expired("sid-001")
        assert store.exists("sid-001")
        daemon.stop()
