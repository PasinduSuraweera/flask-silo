"""
Full-featured Flask-Silo example - File upload + background processing.

Demonstrates all major Flask-Silo features:
  - Session-isolated state with multiple namespaces
  - Per-session file storage with automatic cleanup
  - Background task with progress tracking
  - Busy-check preventing cleanup during processing
  - 410 Gone for expired sessions on data endpoints
  - Session reset

Run:
    pip install flask flask-silo
    python data_processor.py

Test:
    # Upload a file
    curl -X POST -F "file=@data.csv" http://localhost:5000/api/upload
    # Returns: {"message": "Uploaded data.csv", "sid": "abc123..."}

    # Start processing
    curl -X POST -H "X-Session-ID: abc123..." http://localhost:5000/api/process
    # Returns: {"status": "started"}

    # Poll progress
    curl -H "X-Session-ID: abc123..." http://localhost:5000/api/progress
    # Returns: {"running": true, "progress": 30.0, "message": "Step 3/10", ...}

    # Get results (after processing completes)
    curl -H "X-Session-ID: abc123..." http://localhost:5000/api/results
    # Returns: {"results": [...]}
"""

import time

from flask import Flask, jsonify, request
from flask_silo import BackgroundTask, Silo

app = Flask(__name__)

# ── Initialise Silo ───────────────────────────────────────────────────────

silo = Silo(app, ttl=3600)  # 1-hour sessions

# Register state namespaces
silo.register(
    "processing",
    lambda: {
        "filepath": None,
        "filename": None,
        "results": None,
        "task": BackgroundTask("process"),
    },
)

# Per-session file storage
uploads = silo.add_file_store("uploads", "./example_uploads")

# Protect data endpoints (return 410 if session expired)
silo.add_data_endpoints("/api/results", "/api/export")

# Prevent cleanup while a task is running
silo.store.set_busy_check(lambda sid, session: session["processing"]["task"].is_running)


# ── Routes ─────────────────────────────────────────────────────────────────


@app.route("/api/upload", methods=["POST"])
def upload():
    """Upload a file for processing."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    path = uploads.save(silo.sid, f.filename, f)
    state = silo.state("processing")
    state["filepath"] = path
    state["filename"] = f.filename
    return jsonify({"message": f"Uploaded {f.filename}", "sid": silo.sid})


@app.route("/api/process", methods=["POST"])
def start_processing():
    """Start background processing of the uploaded file."""
    state = silo.state("processing")

    if state["task"].is_running:
        return jsonify({"error": "Already processing"}), 409
    if not state["filepath"]:
        return jsonify({"error": "No file uploaded"}), 400

    def do_work(task: BackgroundTask, filepath: str):
        """Simulated processing with progress updates."""
        total_steps = 10
        results = []
        for i in range(total_steps):
            time.sleep(0.5)  # simulate work
            task.update(
                progress=(i + 1) / total_steps * 100,
                message=f"Processing step {i + 1}/{total_steps}",
            )
            task.log(f"Completed step {i + 1}")
            results.append(f"result-{i + 1}")

        # Store results in session state
        # (we need to access the session again from the thread)
        state["results"] = results
        task.complete(f"Processed {total_steps} steps successfully")

    state["task"].start(do_work, state["filepath"])
    return jsonify({"status": "started"})


@app.route("/api/progress")
def progress():
    """Poll background task progress."""
    state = silo.state("processing")
    return jsonify(state["task"].state.to_dict())


@app.route("/api/results")
def results():
    """Get processing results (requires completed task)."""
    state = silo.state("processing")
    if not state["task"].is_complete:
        return jsonify({"error": "Processing not complete"}), 400
    return jsonify(
        {
            "filename": state["filename"],
            "results": state["results"],
            "task": state["task"].state.to_dict(),
        }
    )


@app.route("/api/status")
def status():
    """Get current session status."""
    state = silo.state("processing")
    return jsonify(
        {
            "has_file": state["filepath"] is not None,
            "filename": state["filename"],
            "task_running": state["task"].is_running,
            "task_complete": state["task"].is_complete,
        }
    )


@app.route("/api/reset", methods=["POST"])
def reset():
    """Reset the current session (clear all data and files)."""
    silo.reset_current()
    return jsonify({"message": "Session reset"})


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  Flask-Silo Data Processor Example")
    print("  ─────────────────────────────────")
    print("  POST /api/upload   - upload a file")
    print("  POST /api/process  - start processing")
    print("  GET  /api/progress - poll progress")
    print("  GET  /api/results  - get results")
    print("  GET  /api/status   - session status")
    print("  POST /api/reset    - clear everything\n")
    app.run(debug=True, port=5000)
