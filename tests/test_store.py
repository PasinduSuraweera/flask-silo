"""Tests for SessionStore core functionality.

Covers: creation, namespace isolation, lifecycle (touch/destroy/reset),
callbacks, custom SID generation, lazy namespace init, and thread safety.
"""

import time
import threading

import pytest
from flask_silo import SessionStore, NamespaceError
from flask_silo.errors import SessionBusy


@pytest.fixture
def store():
    s = SessionStore(ttl=10, cleanup_interval=5, expired_retain=30)
    s.register_namespace("cart", lambda: {"items": [], "total": 0.0})
    s.register_namespace("prefs", lambda: {"theme": "dark"})
    return s


# ── Creation & access ──────────────────────────────────────────────────────


class TestSessionCreation:
    def test_get_creates_new_session(self, store):
        session = store.get("sid-001")
        assert "cart" in session
        assert "prefs" in session
        assert "_meta" in session

    def test_get_returns_same_session(self, store):
        s1 = store.get("sid-001")
        s2 = store.get("sid-001")
        assert s1 is s2

    def test_different_sids_isolated(self, store):
        s1 = store.get("sid-001")
        s2 = store.get("sid-002")
        s1["cart"]["items"].append("apple")
        assert s2["cart"]["items"] == []

    def test_meta_populated(self, store):
        session = store.get("sid-001")
        meta = session["_meta"]
        assert meta["sid"] == "sid-001"
        assert isinstance(meta["created_at"], float)
        assert isinstance(meta["last_active"], float)

    def test_generate_sid_default(self, store):
        sid = store.generate_sid()
        assert isinstance(sid, str)
        assert len(sid) >= 16

    def test_custom_sid_generator(self):
        counter = {"n": 0}

        def gen():
            counter["n"] += 1
            return f"custom-{counter['n']:08d}"

        s = SessionStore(sid_generator=gen)
        assert s.generate_sid() == "custom-00000001"
        assert s.generate_sid() == "custom-00000002"


# ── Namespace access ───────────────────────────────────────────────────────


class TestNamespaceAccess:
    def test_get_namespace(self, store):
        cart = store.get_namespace("sid-001", "cart")
        assert cart == {"items": [], "total": 0.0}

    def test_unregistered_namespace_raises(self, store):
        with pytest.raises(NamespaceError, match="orders"):
            store.get_namespace("sid-001", "orders")

    def test_lazy_namespace_init(self, store):
        """Namespaces registered after a session was created are lazily added."""
        store.get("sid-001")
        store.register_namespace("new_ns", lambda: {"val": 42})
        session = store.get("sid-001")
        assert session["new_ns"] == {"val": 42}


# ── Lifecycle ──────────────────────────────────────────────────────────────


class TestSessionLifecycle:
    def test_exists(self, store):
        assert not store.exists("sid-001")
        store.get("sid-001")
        assert store.exists("sid-001")

    def test_touch_updates_timestamp(self, store):
        store.get("sid-001")
        old_ts = store.get("sid-001")["_meta"]["last_active"]
        time.sleep(0.02)
        store.touch("sid-001")
        new_ts = store.get("sid-001")["_meta"]["last_active"]
        assert new_ts > old_ts

    def test_touch_nonexistent_is_noop(self, store):
        store.touch("nonexistent")  # should not raise

    def test_destroy_removes_without_expiry_tracking(self, store):
        store.get("sid-001")
        store.destroy("sid-001")
        assert not store.exists("sid-001")
        assert not store.is_expired("sid-001")

    def test_reset_replaces_state(self, store):
        session = store.get("sid-001")
        session["cart"]["items"].append("apple")
        store.reset("sid-001")
        session = store.get("sid-001")
        assert session["cart"]["items"] == []

    def test_reset_busy_raises(self, store):
        store.set_busy_check(lambda sid, s: True)
        store.get("sid-001")
        with pytest.raises(SessionBusy):
            store.reset("sid-001")

    def test_active_count(self, store):
        assert store.active_count == 0
        store.get("sid-001")
        store.get("sid-002")
        assert store.active_count == 2

    def test_all_sids(self, store):
        store.get("sid-001")
        store.get("sid-002")
        assert set(store.all_sids) == {"sid-001", "sid-002"}


# ── Callbacks ──────────────────────────────────────────────────────────────


class TestCallbacks:
    def test_on_create_fires_for_new_session(self, store):
        created = []
        store.on_create(lambda sid: created.append(sid))
        store.get("sid-001")
        assert created == ["sid-001"]

    def test_on_create_does_not_fire_for_existing(self, store):
        created = []
        store.on_create(lambda sid: created.append(sid))
        store.get("sid-001")
        store.get("sid-001")  # existing
        assert created == ["sid-001"]

    def test_on_expire_fires_on_cleanup(self, store):
        expired = []
        store.on_expire(lambda sid: expired.append(sid))
        store.get("sid-001")
        store._sessions["sid-001"]["_meta"]["last_active"] = (
            time.time() - store.ttl - 1
        )
        store.cleanup()
        assert expired == ["sid-001"]

    def test_multiple_callbacks(self, store):
        log1, log2 = [], []
        store.on_create(lambda sid: log1.append(sid))
        store.on_create(lambda sid: log2.append(sid))
        store.get("sid-001")
        assert log1 == ["sid-001"]
        assert log2 == ["sid-001"]

    def test_callback_exception_does_not_propagate(self, store):
        def bad_cb(sid):
            raise ValueError("boom")

        store.on_create(bad_cb)
        store.get("sid-001")  # should not raise


# ── Thread safety ──────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_access(self, store):
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    sid = f"thread-{n}"
                    session = store.get(sid)
                    session["cart"]["items"].append(i)
                    store.touch(sid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.active_count == 10
