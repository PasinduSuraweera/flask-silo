"""Flask-Silo - Session-isolated state management for Flask APIs.

A production-grade library for managing per-client state in Flask
applications.  Born from real-world data-processing pipelines, Flask-Silo
provides:

* **Session Isolation** - each client gets independent state via
  header-based session IDs.
* **TTL Enforcement** - daemon-thread cleanup with configurable
  time-to-live.
* **410 Gone Pattern** - detect returning expired clients and prompt
  re-upload.
* **Background Tasks** - thread-based task runner with progress tracking.
* **File Management** - per-session upload directories with automatic
  cleanup.

Quick start::

    from flask import Flask, jsonify
    from flask_silo import Silo, BackgroundTask

    app = Flask(__name__)
    silo = Silo(app, ttl=3600)

    silo.register('processing', lambda: {
        'data': None,
        'task': BackgroundTask('process'),
    })

    @app.route('/api/upload', methods=['POST'])
    def upload():
        state = silo.state('processing')
        state['data'] = 'uploaded'
        return jsonify({'sid': silo.sid})

    @app.route('/api/report')
    def report():
        state = silo.state('processing')
        return jsonify(state['data'])

:copyright: (c) 2026.
:license: MIT - see LICENSE file.
"""

from .cleanup import CleanupDaemon
from .errors import NamespaceError, SessionBusy, SessionExpired, SiloError
from .ext import Silo
from .files import FileStore
from .store import SessionStore
from .tasks import BackgroundTask, TaskState

__version__ = "0.1.4"

__all__ = [
    # Extension (main entry point)
    "Silo",
    # Core components
    "SessionStore",
    "CleanupDaemon",
    "BackgroundTask",
    "TaskState",
    "FileStore",
    # Exceptions
    "SiloError",
    "SessionExpired",
    "SessionBusy",
    "NamespaceError",
    # Metadata
    "__version__",
]
