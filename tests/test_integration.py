"""Integration tests for the Flask-Silo extension.

Covers: session creation, header propagation, client isolation, 410 Gone
on expired data endpoints, status endpoints after expiry, re-upload
lifecycle, reset, query-param fallback, non-API routes, and file-store
cleanup on expiry.
"""

import time

import pytest
from flask import Flask, jsonify
from flask_silo import BackgroundTask, Silo

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def app_and_silo(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True

    silo = Silo(
        app,
        ttl=5,
        cleanup_interval=60,  # manual cleanup in tests
        expired_retain=30,
        auto_cleanup=False,
    )

    silo.register("data", lambda: {"items": [], "processed": False})
    silo.register("task_ns", lambda: {"task": BackgroundTask("process")})

    silo.add_data_endpoints("/api/report", "/api/export")

    @app.route("/api/status")
    def status():
        data = silo.state("data")
        return jsonify({"items": len(data["items"]), "processed": data["processed"]})

    @app.route("/api/upload", methods=["POST"])
    def upload():
        data = silo.state("data")
        data["items"].append("new_item")
        return jsonify({"count": len(data["items"]), "sid": silo.sid})

    @app.route("/api/report")
    def report():
        data = silo.state("data")
        return jsonify({"items": data["items"]})

    @app.route("/api/export")
    def export():
        return jsonify({"data": "exported"})

    @app.route("/api/reset", methods=["POST"])
    def reset():
        silo.reset_current()
        return jsonify({"message": "reset"})

    @app.route("/health")
    def health():
        return "ok"

    return app, silo


@pytest.fixture
def client(app_and_silo):
    app, _ = app_and_silo
    return app.test_client()


# ── Core integration tests ─────────────────────────────────────────────────


class TestFlaskIntegration:
    def test_session_created_on_first_request(self, client, app_and_silo):
        _, silo = app_and_silo
        res = client.get("/api/status")
        assert res.status_code == 200
        sid = res.headers.get("X-Session-ID")
        assert sid
        assert silo.store.exists(sid)

    def test_session_reused_with_header(self, client):
        res1 = client.post("/api/upload")
        sid = res1.headers.get("X-Session-ID")

        res2 = client.get("/api/status", headers={"X-Session-ID": sid})
        assert res2.get_json()["items"] == 1

    def test_clients_isolated(self, app_and_silo):
        app, _ = app_and_silo
        c1 = app.test_client()
        c2 = app.test_client()

        r1 = c1.post("/api/upload")
        sid1 = r1.headers.get("X-Session-ID")

        r2 = c2.get("/api/status")
        sid2 = r2.headers.get("X-Session-ID")

        assert sid1 != sid2
        assert r2.get_json()["items"] == 0

    def test_410_on_expired_data_endpoint(self, client, app_and_silo):
        _, silo = app_and_silo
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        silo.store._sessions[sid]["_meta"]["last_active"] = time.time() - 100
        silo.store.cleanup()

        res = client.get("/api/report", headers={"X-Session-ID": sid})
        assert res.status_code == 410
        assert res.get_json()["error"] == "session_expired"

    def test_410_message_present(self, client, app_and_silo):
        _, silo = app_and_silo
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        silo.store._sessions[sid]["_meta"]["last_active"] = time.time() - 100
        silo.store.cleanup()

        res = client.get("/api/export", headers={"X-Session-ID": sid})
        assert res.status_code == 410
        body = res.get_json()
        assert "expired" in body["message"].lower()

    def test_status_works_after_expiry(self, client, app_and_silo):
        _, silo = app_and_silo
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        silo.store._sessions[sid]["_meta"]["last_active"] = time.time() - 100
        silo.store.cleanup()

        res = client.get("/api/status", headers={"X-Session-ID": sid})
        assert res.status_code == 200  # creates new session

    def test_reupload_after_expiry(self, client, app_and_silo):
        _, silo = app_and_silo
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        silo.store._sessions[sid]["_meta"]["last_active"] = time.time() - 100
        silo.store.cleanup()
        assert silo.store.is_expired(sid)

        res = client.post("/api/upload", headers={"X-Session-ID": sid})
        assert res.status_code == 200
        assert not silo.store.is_expired(sid)

    def test_reset_endpoint(self, client):
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        res = client.get("/api/status", headers={"X-Session-ID": sid})
        assert res.get_json()["items"] == 1

        client.post("/api/reset", headers={"X-Session-ID": sid})

        res = client.get("/api/status", headers={"X-Session-ID": sid})
        assert res.get_json()["items"] == 0

    def test_query_param_fallback(self, client):
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        res = client.get(f"/api/status?_sid={sid}")
        assert res.get_json()["items"] == 1

    def test_non_api_routes_skip_session(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert "X-Session-ID" not in res.headers


# ── File store integration ─────────────────────────────────────────────────


class TestFileStoreIntegration:
    def test_file_cleanup_on_expiry(self, app_and_silo, tmp_path):
        app, silo = app_and_silo
        fs = silo.add_file_store("uploads", str(tmp_path / "uploads"))

        client = app.test_client()
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        fs.save(sid, "test.txt", b"hello")
        assert fs.get_path(sid, "test.txt") is not None

        silo.store._sessions[sid]["_meta"]["last_active"] = time.time() - 100
        silo.store.cleanup()

        assert fs.get_path(sid, "test.txt") is None

    def test_file_cleanup_on_reset(self, app_and_silo, tmp_path):
        app, silo = app_and_silo
        fs = silo.add_file_store("docs", str(tmp_path / "docs"))

        client = app.test_client()
        res = client.post("/api/upload")
        sid = res.headers.get("X-Session-ID")

        fs.save(sid, "doc.pdf", b"pdf-data")
        assert fs.get_path(sid, "doc.pdf") is not None

        client.post("/api/reset", headers={"X-Session-ID": sid})

        assert fs.get_path(sid, "doc.pdf") is None


# ── Factory pattern ────────────────────────────────────────────────────────


class TestFactoryPattern:
    def test_init_app_deferred(self):
        silo = Silo(ttl=60, auto_cleanup=False)
        silo.register("ns", lambda: {"v": 1})

        app = Flask(__name__)
        app.config["TESTING"] = True
        silo.init_app(app)

        @app.route("/api/test")
        def test_route():
            return jsonify(silo.state("ns"))

        client = app.test_client()
        res = client.get("/api/test")
        assert res.status_code == 200
        assert res.get_json()["v"] == 1
