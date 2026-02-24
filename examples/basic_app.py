"""
Basic Flask-Silo example — Session-isolated counter API.

Each client (identified by X-Session-ID header) gets their own counter.
Sessions expire after 5 minutes of inactivity.

Run:
    pip install flask flask-silo
    python basic_app.py

Test:
    # First client
    curl -X POST http://localhost:5000/api/increment
    # Returns: {"sid": "abc123...", "value": 1}

    # Same client (pass the SID back)
    curl -H "X-Session-ID: abc123..." http://localhost:5000/api/count
    # Returns: {"value": 1}

    # Different client (no header = new session)
    curl http://localhost:5000/api/count
    # Returns: {"value": 0}
"""

from flask import Flask, jsonify
from flask_silo import Silo

app = Flask(__name__)

# Initialise Flask-Silo with 5-minute TTL
silo = Silo(app, ttl=300)

# Register a "counter" namespace — each client gets their own independent copy
silo.register("counter", lambda: {"value": 0, "history": []})

# These endpoints return 410 Gone if the session expired
silo.add_data_endpoints("/api/count", "/api/history")


@app.route("/api/increment", methods=["POST"])
def increment():
    """Increment the counter for the current session."""
    state = silo.state("counter")
    state["value"] += 1
    state["history"].append(state["value"])
    return jsonify({"value": state["value"], "sid": silo.sid})


@app.route("/api/count")
def get_count():
    """Get the current counter value."""
    state = silo.state("counter")
    return jsonify({"value": state["value"]})


@app.route("/api/history")
def get_history():
    """Get the full increment history."""
    state = silo.state("counter")
    return jsonify({"history": state["history"]})


@app.route("/api/reset", methods=["POST"])
def reset():
    """Reset the current session's counter."""
    silo.reset_current()
    return jsonify({"message": "Counter reset"})


if __name__ == "__main__":
    print("\n  Flask-Silo Counter Example")
    print("  POST /api/increment  — increment counter")
    print("  GET  /api/count      — current value")
    print("  GET  /api/history    — increment history")
    print("  POST /api/reset      — reset counter\n")
    app.run(debug=True, port=5000)
