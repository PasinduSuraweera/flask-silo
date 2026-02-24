"""Tests for RedisStorage using a mock Redis client."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

from flask_silo.redis_storage import RedisStorage


def _make_redis_mock() -> MagicMock:
    """Create a mock Redis client with sensible defaults."""
    mock = MagicMock()
    mock.get.return_value = None
    mock.exists.return_value = 0
    mock.scan_iter.return_value = iter([])
    mock.zscore.return_value = None
    mock.zcard.return_value = 0
    return mock


class TestRedisStorageInit:
    """Test constructor and key prefixing."""

    def test_default_prefix(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        assert storage._prefix == "flask_silo"
        assert storage._session_prefix == "flask_silo:session:"
        assert storage._expired_key == "flask_silo:expired"

    def test_custom_prefix(self):
        r = _make_redis_mock()
        storage = RedisStorage(r, prefix="myapp")
        assert storage._session_prefix == "myapp:session:"
        assert storage._expired_key == "myapp:expired"

    def test_session_key_helper(self):
        r = _make_redis_mock()
        storage = RedisStorage(r, prefix="test")
        assert storage._session_key("abc") == "test:session:abc"


class TestRedisStorageSessions:
    """Test session CRUD operations."""

    def test_get_session_missing(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        assert storage.get_session("no-such-sid") is None
        r.get.assert_called_once_with("flask_silo:session:no-such-sid")

    def test_get_session_bytes(self):
        r = _make_redis_mock()
        data = {"key": "value", "count": 42}
        r.get.return_value = json.dumps(data).encode()
        storage = RedisStorage(r)
        result = storage.get_session("sid1")
        assert result == data

    def test_get_session_str(self):
        r = _make_redis_mock()
        data = {"key": "value"}
        r.get.return_value = json.dumps(data)
        storage = RedisStorage(r)
        result = storage.get_session("sid1")
        assert result == data

    def test_set_session_no_ttl(self):
        r = _make_redis_mock()
        storage = RedisStorage(r, session_ttl=0)
        storage.set_session("sid1", {"x": 1})
        r.set.assert_called_once_with("flask_silo:session:sid1", json.dumps({"x": 1}))
        r.setex.assert_not_called()

    def test_set_session_with_ttl(self):
        r = _make_redis_mock()
        storage = RedisStorage(r, session_ttl=3600)
        storage.set_session("sid1", {"x": 1})
        r.setex.assert_called_once_with(
            "flask_silo:session:sid1", 3600, json.dumps({"x": 1})
        )
        r.set.assert_not_called()

    def test_delete_session(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        storage.delete_session("sid1")
        r.delete.assert_called_once_with("flask_silo:session:sid1")

    def test_has_session_true(self):
        r = _make_redis_mock()
        r.exists.return_value = 1
        storage = RedisStorage(r)
        assert storage.has_session("sid1") is True

    def test_has_session_false(self):
        r = _make_redis_mock()
        r.exists.return_value = 0
        storage = RedisStorage(r)
        assert storage.has_session("sid1") is False


class TestRedisStorageBulk:
    """Test bulk/scan operations."""

    def test_all_sessions(self):
        r = _make_redis_mock()
        data1 = {"a": 1}
        data2 = {"b": 2}
        r.scan_iter.return_value = iter(
            [
                b"flask_silo:session:s1",
                b"flask_silo:session:s2",
            ]
        )
        r.get.side_effect = [
            json.dumps(data1).encode(),
            json.dumps(data2).encode(),
        ]
        storage = RedisStorage(r)
        result = storage.all_sessions()
        assert len(result) == 2
        assert ("s1", data1) in result
        assert ("s2", data2) in result

    def test_all_sessions_str_keys(self):
        """Test with string (not bytes) keys from scan_iter."""
        r = _make_redis_mock()
        data = {"val": 99}
        r.scan_iter.return_value = iter(["flask_silo:session:s1"])
        r.get.return_value = json.dumps(data)
        storage = RedisStorage(r)
        result = storage.all_sessions()
        assert result == [("s1", data)]

    def test_all_sessions_skips_deleted(self):
        """If a key disappears between SCAN and GET, skip it."""
        r = _make_redis_mock()
        r.scan_iter.return_value = iter([b"flask_silo:session:gone"])
        r.get.return_value = None
        storage = RedisStorage(r)
        result = storage.all_sessions()
        assert result == []

    def test_session_count(self):
        r = _make_redis_mock()
        r.scan_iter.return_value = iter([b"k1", b"k2", b"k3"])
        storage = RedisStorage(r)
        assert storage.session_count() == 3

    def test_session_count_empty(self):
        r = _make_redis_mock()
        r.scan_iter.return_value = iter([])
        storage = RedisStorage(r)
        assert storage.session_count() == 0

    def test_all_sids(self):
        r = _make_redis_mock()
        r.scan_iter.return_value = iter(
            [
                b"flask_silo:session:alpha",
                b"flask_silo:session:beta",
            ]
        )
        storage = RedisStorage(r)
        sids = storage.all_sids()
        assert set(sids) == {"alpha", "beta"}

    def test_all_sids_str_keys(self):
        r = _make_redis_mock()
        r.scan_iter.return_value = iter(["flask_silo:session:x1"])
        storage = RedisStorage(r)
        assert storage.all_sids() == ["x1"]


class TestRedisStorageExpired:
    """Test expired tracking operations."""

    def test_mark_expired(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        storage.mark_expired("sid1", 1000.0)
        r.zadd.assert_called_once_with("flask_silo:expired", {"sid1": 1000.0})

    def test_is_expired_true(self):
        r = _make_redis_mock()
        r.zscore.return_value = 1000.0
        storage = RedisStorage(r)
        assert storage.is_expired("sid1") is True

    def test_is_expired_false(self):
        r = _make_redis_mock()
        r.zscore.return_value = None
        storage = RedisStorage(r)
        assert storage.is_expired("sid1") is False

    def test_clear_expired(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        storage.clear_expired("sid1")
        r.zrem.assert_called_once_with("flask_silo:expired", "sid1")

    def test_prune_expired(self):
        r = _make_redis_mock()
        storage = RedisStorage(r)
        before = time.time()
        storage.prune_expired(3600.0)
        after = time.time()
        # Should call zremrangebyscore with cutoff = now - max_age
        call_args = r.zremrangebyscore.call_args
        assert call_args[0][0] == "flask_silo:expired"
        assert call_args[0][1] == "-inf"
        cutoff = call_args[0][2]
        assert before - 3600.0 <= cutoff <= after - 3600.0

    def test_expired_count(self):
        r = _make_redis_mock()
        r.zcard.return_value = 5
        storage = RedisStorage(r)
        assert storage.expired_count() == 5

    def test_expired_count_zero(self):
        r = _make_redis_mock()
        r.zcard.return_value = 0
        storage = RedisStorage(r)
        assert storage.expired_count() == 0

    def test_expired_count_none(self):
        """zcard returning None should be treated as 0."""
        r = _make_redis_mock()
        r.zcard.return_value = None
        storage = RedisStorage(r)
        assert storage.expired_count() == 0
