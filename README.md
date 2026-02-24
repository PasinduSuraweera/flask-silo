# Flask-Silo

**Session-isolated state management for Flask APIs.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-60%2B%20passing-brightgreen.svg)]()
[![Typed](https://img.shields.io/badge/typing-PEP%20561-blueviolet.svg)]()

---

**Flask-Silo** gives each API client its own isolated state - like giving every user their own private workspace on the server. Born from production data-processing pipelines where multiple users upload files, run background tasks, and generate reports simultaneously.

## The Problem

Flask apps handling stateful workflows (file upload -> process -> report) need per-client state isolation. Without it:

- User A's upload overwrites User B's data
- Background tasks corrupt each other's progress
- Session expiry leaves orphaned files on disk
- Module-level globals create race conditions

**Flask-Silo** solves all of this with a clean, typed API.

## Features

| Feature | Description |
|---|---|
| **Session Isolation** | Each client gets independent state via `X-Session-ID` header |
| **TTL Enforcement** | Daemon thread automatically cleans up idle sessions |
| **410 Gone Pattern** | Expired clients get `410` on data endpoints (not a silent empty session) |
| **Background Tasks** | Thread-based runner with progress %, log entries, and completion status |
| **File Management** | Per-session upload directories with automatic cleanup on expiry |
| **Busy Protection** | Custom predicates prevent cleanup of sessions with running tasks |
| **Lifecycle Hooks** | `on_create` / `on_expire` callbacks for monitoring and side effects |
| **Factory Pattern** | Supports Flask's `init_app()` factory pattern |
| **Fully Typed** | PEP 561 compliant with comprehensive type annotations |

## Quick Start

### Installation

```bash
pip install flask-silo
```

### Minimal Example

```python
from flask import Flask, jsonify
from flask_silo import Silo

app = Flask(__name__)
silo = Silo(app, ttl=3600)  # 1-hour sessions

# Register a namespace - each client gets their own copy
silo.register('counter', lambda: {'value': 0})

@app.route('/api/increment', methods=['POST'])
def increment():
    state = silo.state('counter')
    state['value'] += 1
    return jsonify({'value': state['value'], 'sid': silo.sid})

@app.route('/api/count')
def count():
    return jsonify({'value': silo.state('counter')['value']})
```

```bash
# Client A
curl -X POST http://localhost:5000/api/increment
# -> {"sid": "a1b2c3...", "value": 1}

# Client B (different session)
curl http://localhost:5000/api/count
# -> {"value": 0}  <-- isolated!
```

## Full-Featured Example

```python
from flask import Flask, jsonify, request
from flask_silo import Silo, BackgroundTask

app = Flask(__name__)
silo = Silo(app, ttl=3600)

# Multiple namespaces per session
silo.register('processing', lambda: {
    'data': None,
    'results': None,
    'task': BackgroundTask('process'),
})

# Per-session file storage (auto-cleaned on expiry)
uploads = silo.add_file_store('uploads', './uploads')

# These endpoints return 410 if session expired
silo.add_data_endpoints('/api/results', '/api/export')

# Don't clean up sessions with running tasks
silo.store.set_busy_check(
    lambda sid, s: s['processing']['task'].is_running
)

@app.route('/api/upload', methods=['POST'])
def upload():
    f = request.files['file']
    path = uploads.save(silo.sid, f.filename, f)
    silo.state('processing')['data'] = path
    return jsonify({'message': f'Uploaded {f.filename}'})

@app.route('/api/process', methods=['POST'])
def process():
    state = silo.state('processing')

    def work(task, filepath):
        for i in range(10):
            # ... do work ...
            task.update(progress=(i+1)*10, message=f'Step {i+1}/10')
            task.log(f'Completed step {i+1}')
        task.complete('Done!')

    state['task'].start(work, state['data'])
    return jsonify({'status': 'started'})

@app.route('/api/progress')
def progress():
    return jsonify(silo.state('processing')['task'].state.to_dict())

@app.route('/api/reset', methods=['POST'])
def reset():
    silo.reset_current()  # clears state + files
    return jsonify({'message': 'Reset'})
```

## Architecture

```
+-------------------------------------------------------------+
|                        Flask App                            |
|                                                             |
|  +-------------------------------------------------------+  |
|  |                    Silo (Extension)                   |  |
|  |                                                       |  |
|  |  before_request --> Extract SID --> Load/Create State |  |
|  |  after_request  --> Set X-Session-ID header           |  |
|  |                                                       |  |
|  |  +-------------+  +--------------+  +-------------+   |  |
|  |  |SessionStore |  |CleanupDaemon |  | FileStore(s)|   |  |
|  |  |             |  |              |  |             |   |  |
|  |  | _sessions{} |<-| cleanup()    |  | base_dir/   |   |  |
|  |  | _expired{}  |  | every 60s    |--| {sid}/      |   |  |
|  |  | _factories{}|  |              |  |   files...  |   |  |
|  |  +-------------+  +--------------+  +-------------+   |  |
|  |                                                       |  |
|  |  Session Dict:                                        |  |
|  |  +--------------------------------------------------+ |  |
|  |  | { 'namespace_a': {...},                          | |  |
|  |  |   'namespace_b': {..., task: BackgroundTask},    | |  |
|  |  |   '_meta': {created_at, last_active, sid} }      | |  |
|  |  +--------------------------------------------------+ |  |
|  +-------------------------------------------------------+  |
+-------------------------------------------------------------+
```

## API Reference

### `Silo` - Flask Extension

```python
silo = Silo(
    app=None,              # Flask app (or use init_app)
    ttl=3600,              # Session lifetime (seconds)
    cleanup_interval=60,   # Cleanup frequency (seconds)
    expired_retain=7200,   # Remember expired SIDs for 410 (seconds)
    header="X-Session-ID", # Header name for session ID
    query_param="_sid",    # Query param fallback (for download links)
    min_sid_length=16,     # Minimum SID length to accept
    auto_cleanup=True,     # Start cleanup daemon automatically
    api_prefix="/api/",    # URL prefix triggering session handling
)
```

| Method | Description |
|---|---|
| `silo.register(name, factory)` | Register a state namespace |
| `silo.state(namespace)` | Get namespace state for current request |
| `silo.sid` | Current session ID (property) |
| `silo.add_file_store(name, dir)` | Add per-session file storage |
| `silo.file_store(name)` | Get a registered file store |
| `silo.add_data_endpoints(*paths)` | Mark endpoints for 410 on expiry |
| `silo.reset_current()` | Reset current session + cleanup files |
| `silo.init_app(app)` | Deferred initialisation (factory pattern) |
| `silo.stop()` | Stop cleanup daemon |

### `SessionStore` - Core State Manager

```python
store = SessionStore(ttl=3600, cleanup_interval=60, expired_retain=7200)
store.register_namespace('ns', lambda: {'key': 'value'})
```

| Method | Description |
|---|---|
| `store.get(sid)` | Get/create full session dict |
| `store.get_namespace(sid, ns)` | Get specific namespace state |
| `store.touch(sid)` | Reset TTL timer |
| `store.exists(sid)` | Check if session is active |
| `store.is_expired(sid)` | Check if SID was recently expired |
| `store.cleanup()` | Run cleanup pass, returns expired SIDs |
| `store.reset(sid)` | Reset to fresh state |
| `store.destroy(sid)` | Remove without expiry tracking |
| `store.set_busy_check(fn)` | Set cleanup veto predicate |
| `store.on_create(callback)` | Register creation callback |
| `store.on_expire(callback)` | Register expiry callback |
| `store.active_count` | Number of active sessions |
| `store.expired_count` | Number of tracked expired SIDs |

### `BackgroundTask` - Progress-Tracked Threading

```python
task = BackgroundTask('classify')

def work(task, filepath):
    task.update(progress=50, message='Halfway')
    task.log('Processing batch 5/10')
    task.complete('All done')

task.start(work, '/path/to/file')
```

| Method / Property | Description |
|---|---|
| `task.start(target, *args, **kwargs)` | Run target in daemon thread |
| `task.update(progress, message)` | Update progress (0–100) |
| `task.log(message)` | Append log entry |
| `task.complete(message)` | Mark as successfully complete |
| `task.fail(error)` | Mark as failed |
| `task.reset()` | Reset for re-use |
| `task.state` | `TaskState` snapshot |
| `task.is_running` | Currently executing? |
| `task.is_complete` | Finished successfully? |
| `task.is_failed` | Failed with error? |

### `FileStore` - Per-Session File Management

```python
fs = FileStore('/tmp/uploads')
path = fs.save('sid-123', 'report.xlsx', file_obj)
fs.cleanup('sid-123')
```

| Method | Description |
|---|---|
| `fs.session_dir(sid)` | Get/create session directory |
| `fs.save(sid, filename, data)` | Save file (bytes or file-like) |
| `fs.get_path(sid, filename)` | Get path or `None` |
| `fs.list_files(sid)` | List filenames in session dir |
| `fs.cleanup(sid)` | Remove session's files |
| `fs.cleanup_all()` | Remove all session dirs |
| `fs.total_size_bytes` | Total disk usage |

## The 410 Gone Pattern

When a session expires, instead of silently creating a new empty session, Flask-Silo tracks the old SID and returns `410 Gone` on data-dependent endpoints:

```
Client                    Server
  |                         |
  |-- Upload file --------> |  Session created (SID: abc)
  |                         |
  |   ... 1 hour passes ... |
  |                         |  <-- Cleanup daemon expires SID abc
  |                         |
  |-- GET /api/report ----> |  410 Gone (SID abc was expired)
  |                         |
  |-- Upload file --------> |  Session re-created (same SID)
```

This enables clean frontend handling:

```javascript
if (response.status === 410) {
  clearSession();
  showToast('Session expired - please re-upload');
  redirect('/upload');
}
```

## Limitations & When Not to Use

Flask-Silo stores session state **in-process** (Python dicts) and files on **local disk**. This is an intentional design choice for simplicity, but it comes with trade-offs you should understand:

### Single-process only

If you deploy with multiple workers (`gunicorn -w 4`), each worker gets its own `_sessions` dict. A request hitting Worker A cannot see sessions created by Worker B. **You must run with a single worker** (`gunicorn -w 1`) or use a sticky-session load balancer.

For multi-worker / multi-server deployments, use **Flask-Session** backed by Redis or a database instead.

### In-memory state is volatile

All session data lives in process memory. If the server restarts, all sessions are lost. There is no persistence layer.

### Not a replacement for a task queue

`BackgroundTask` runs work in daemon threads inside the web process. This is fine for lightweight jobs (data transformation, report generation), but it is **not** a substitute for Celery or RQ if you need:

- Retries, rate limiting, or scheduling
- Tasks that survive server restarts
- Distributed execution across multiple machines

### Concurrent mutations to the same SID

The `SessionStore` lock protects session creation and cleanup, but the returned session dict is a plain mutable reference. If two concurrent requests share the same SID and mutate the same namespace simultaneously, there is no per-namespace locking. In practice this is rare (one client = one SID, requests are serial), but it is not guarded against.

### When Flask-Silo is a good fit

- Internal tools, prototypes, and dashboards with a small number of concurrent users
- Stateful workflows (upload -> process -> download) where setting up Redis/Celery is overkill
- Single-process deployments behind Gunicorn, Waitress, or the Flask dev server
- Applications where losing sessions on restart is acceptable

### When to use something else

| Need | Use instead |
|---|---|
| Multi-worker / multi-server sessions | Flask-Session + Redis |
| Durable background jobs | Celery or RQ |
| Shared file storage across servers | AWS S3 / MinIO / shared volume |
| Persistent state across restarts | Database (PostgreSQL, SQLite) |

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov=flask_silo --cov-report=html

# Type checking
mypy src/flask_silo
```

## How It Was Born

This library was extracted from a production **QSR Analysis Hub** - a Flask + Next.js application that analyses restaurant void bills, deleted items, and staff discounts. The server needed to handle multiple concurrent users, each uploading Excel files, running AI classification tasks, reviewing results, and exporting reports - all with complete session isolation.

The patterns that emerged (session stores, TTL cleanup daemons, 410 Gone for expired sessions, background task progress tracking, per-session file storage) proved generic enough to become a reusable library.

## License

[MIT](LICENSE)
