"""Tests for the pluggable storage backends.

Covers: SiloStorage interface compliance for InMemoryStorage, session
CRUD, expired-SID tracking, and pruning.
"""

import time

import pytest

from flask_silo.storage import InMemoryStorage, SiloStorage

# ── InMemoryStorage ───────────────────────────────────────────────────────


@pytest.fixture
def mem():
    return InMemoryStorage()


class TestInMemoryStorageInterface:
    """Ensure InMemoryStorage satisfies the SiloStorage ABC."""

    def test_is_silo_storage(self, mem):
        assert isinstance(mem, SiloStorage)


class TestInMemorySessionOps:
    def test_get_missing_returns_none(self, mem):
        assert mem.get_session("nope") is None

    def test_set_and_get(self, mem):
        data = {"key": "value"}
        mem.set_session("sid-1", data)
        assert mem.get_session("sid-1") is data  # reference equality

    def test_has_session(self, mem):
        assert not mem.has_session("sid-1")
        mem.set_session("sid-1", {"k": "v"})
        assert mem.has_session("sid-1")

    def test_delete_session(self, mem):
        mem.set_session("sid-1", {"k": "v"})
        mem.delete_session("sid-1")
        assert not mem.has_session("sid-1")

    def test_delete_missing_is_noop(self, mem):
        mem.delete_session("nope")  # should not raise

    def test_all_sessions(self, mem):
        mem.set_session("a", {"v": 1})
        mem.set_session("b", {"v": 2})
        result = dict(mem.all_sessions())
        assert result == {"a": {"v": 1}, "b": {"v": 2}}

    def test_all_sessions_returns_snapshot(self, mem):
        mem.set_session("a", {"v": 1})
        snapshot = mem.all_sessions()
        mem.delete_session("a")
        # snapshot should still contain the item
        assert len(snapshot) == 1

    def test_session_count(self, mem):
        assert mem.session_count() == 0
        mem.set_session("a", {})
        mem.set_session("b", {})
        assert mem.session_count() == 2

    def test_all_sids(self, mem):
        mem.set_session("x", {})
        mem.set_session("y", {})
        assert set(mem.all_sids()) == {"x", "y"}


class TestInMemoryExpiredTracking:
    def test_not_expired_initially(self, mem):
        assert not mem.is_expired("sid-1")

    def test_mark_and_check_expired(self, mem):
        mem.mark_expired("sid-1", time.time())
        assert mem.is_expired("sid-1")

    def test_clear_expired(self, mem):
        mem.mark_expired("sid-1", time.time())
        mem.clear_expired("sid-1")
        assert not mem.is_expired("sid-1")

    def test_prune_expired(self, mem):
        old_ts = time.time() - 100
        recent_ts = time.time()
        mem.mark_expired("old-sid", old_ts)
        mem.mark_expired("new-sid", recent_ts)
        mem.prune_expired(50)  # prune records older than 50s
        assert not mem.is_expired("old-sid")
        assert mem.is_expired("new-sid")

    def test_expired_count(self, mem):
        assert mem.expired_count() == 0
        mem.mark_expired("a", time.time())
        mem.mark_expired("b", time.time())
        assert mem.expired_count() == 2

    def test_clear_only_affects_target(self, mem):
        mem.mark_expired("a", time.time())
        mem.mark_expired("b", time.time())
        mem.clear_expired("a")
        assert not mem.is_expired("a")
        assert mem.is_expired("b")


# ── SessionStore integration with storage ─────────────────────────────────


class TestSessionStoreWithStorage:
    """Verify SessionStore properly delegates to the storage backend."""

    def test_explicit_storage_used(self):
        from flask_silo import SessionStore

        storage = InMemoryStorage()
        store = SessionStore(storage=storage)
        store.register_namespace("ns", lambda: {"v": 0})
        store.get("sid-1")
        assert storage.has_session("sid-1")

    def test_default_storage_is_in_memory(self):
        from flask_silo import SessionStore

        store = SessionStore()
        assert isinstance(store.storage, InMemoryStorage)

    def test_save_persists_session(self):
        from flask_silo import SessionStore

        storage = InMemoryStorage()
        store = SessionStore(storage=storage)
        store.register_namespace("ns", lambda: {"v": 0})
        session = store.get("sid-1")
        session["ns"]["v"] = 42
        store.save("sid-1", session)
        assert storage.get_session("sid-1")["ns"]["v"] == 42
