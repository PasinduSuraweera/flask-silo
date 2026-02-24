"""Redis-backed storage for multi-worker Flask-Silo deployments.

Requires the ``redis`` package::

    pip install flask-silo[redis]

Usage::

    import redis
    from flask_silo import Silo
    from flask_silo.redis_storage import RedisStorage

    r = redis.Redis(host="localhost", port=6379, db=0)
    storage = RedisStorage(r)
    silo = Silo(app, storage=storage)

.. warning::

    Session data must be **JSON-serialisable**.  Objects like
    :class:`~flask_silo.tasks.BackgroundTask` cannot be stored in Redis.
    Use a task queue (Celery, RQ) for background work in multi-worker
    deployments.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .storage import SiloStorage


class RedisStorage(SiloStorage):
    """Redis-backed session storage for multi-process deployments.

    Sessions are stored as JSON strings in individual Redis keys.
    Expired-SID tracking uses a Redis sorted set with timestamps as
    scores, enabling efficient range-based pruning.

    Parameters
    ----------
    redis_client:
        A ``redis.Redis`` (or compatible) client instance.
    prefix:
        Key prefix for all Redis keys (default ``flask_silo``).
    session_ttl:
        Optional Redis-level TTL (seconds) for session keys.  This is a
        safety net **in addition to** Flask-Silo's application-level TTL
        cleanup.  Set to ``0`` to disable (default).

    Example::

        import redis
        from flask_silo.redis_storage import RedisStorage

        r = redis.Redis(host="localhost", port=6379, db=0)
        storage = RedisStorage(r, prefix="myapp", session_ttl=7200)
    """

    __slots__ = (
        "_redis",
        "_prefix",
        "_session_prefix",
        "_expired_key",
        "_session_ttl",
    )

    def __init__(
        self,
        redis_client: Any,
        *,
        prefix: str = "flask_silo",
        session_ttl: int = 0,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._session_prefix = f"{prefix}:session:"
        self._expired_key = f"{prefix}:expired"
        self._session_ttl = session_ttl

    # -- Helpers -----------------------------------------------------------

    def _session_key(self, sid: str) -> str:
        """Build the Redis key for a session."""
        return f"{self._session_prefix}{sid}"

    # -- Sessions ----------------------------------------------------------

    def get_session(self, sid: str) -> dict[str, Any] | None:
        """Retrieve a session from Redis (deserialised copy)."""
        data = self._redis.get(self._session_key(sid))
        if data is None:
            return None
        raw: str = data.decode() if isinstance(data, bytes) else data
        return json.loads(raw)  # type: ignore[no-any-return]

    def set_session(self, sid: str, data: dict[str, Any]) -> None:
        """Store a session as a JSON string in Redis."""
        key = self._session_key(sid)
        payload = json.dumps(data)
        if self._session_ttl:
            self._redis.setex(key, self._session_ttl, payload)
        else:
            self._redis.set(key, payload)

    def delete_session(self, sid: str) -> None:
        """Delete a session key from Redis."""
        self._redis.delete(self._session_key(sid))

    def has_session(self, sid: str) -> bool:
        """Check whether a session key exists in Redis."""
        return bool(self._redis.exists(self._session_key(sid)))

    def all_sessions(self) -> list[tuple[str, dict[str, Any]]]:
        """Scan for all session keys and deserialise them."""
        result: list[tuple[str, dict[str, Any]]] = []
        prefix_len = len(self._session_prefix)
        for key in self._redis.scan_iter(f"{self._session_prefix}*"):
            raw_key: str = key.decode() if isinstance(key, bytes) else key
            sid = raw_key[prefix_len:]
            data = self._redis.get(key)
            if data:
                raw_val: str = data.decode() if isinstance(data, bytes) else data
                result.append((sid, json.loads(raw_val)))
        return result

    def session_count(self) -> int:
        """Count session keys via SCAN (no KEYS *)."""
        count = 0
        for _ in self._redis.scan_iter(f"{self._session_prefix}*"):
            count += 1
        return count

    def all_sids(self) -> list[str]:
        """Return all session IDs via SCAN."""
        sids: list[str] = []
        prefix_len = len(self._session_prefix)
        for key in self._redis.scan_iter(f"{self._session_prefix}*"):
            raw_key: str = key.decode() if isinstance(key, bytes) else key
            sids.append(raw_key[prefix_len:])
        return sids

    # -- Expired tracking --------------------------------------------------

    def mark_expired(self, sid: str, timestamp: float) -> None:
        """Add SID to the expired sorted set with timestamp as score."""
        self._redis.zadd(self._expired_key, {sid: timestamp})

    def is_expired(self, sid: str) -> bool:
        """Check whether SID is in the expired sorted set."""
        return self._redis.zscore(self._expired_key, sid) is not None

    def clear_expired(self, sid: str) -> None:
        """Remove SID from the expired sorted set."""
        self._redis.zrem(self._expired_key, sid)

    def prune_expired(self, max_age: float) -> None:
        """Remove expired records older than *max_age* seconds."""
        cutoff = time.time() - max_age
        self._redis.zremrangebyscore(self._expired_key, "-inf", cutoff)

    def expired_count(self) -> int:
        """Return the cardinality of the expired sorted set."""
        count = self._redis.zcard(self._expired_key)
        return count if count else 0
