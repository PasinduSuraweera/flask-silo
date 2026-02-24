# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-02-24

### Added

- `SessionStore` — thread-safe session state manager with TTL enforcement
  - Namespace-based state isolation
  - Expired-SID tracking for the 410 Gone pattern
  - Configurable busy-check predicates to prevent cleanup of active sessions
  - Lifecycle callbacks (`on_create`, `on_expire`)
  - Lazy namespace initialisation for late registrations
  - Custom SID generator support
- `CleanupDaemon` — background daemon thread for periodic stale-session purging
  - Interruptible sleep via `threading.Event`
  - Idempotent start/stop
- `BackgroundTask` — threaded task runner with progress tracking
  - Progress percentage, status messages, and log entries
  - Auto-complete on return, explicit complete/fail methods
  - State snapshots via `TaskState` dataclass
  - Progress clamping and timestamp tracking
- `FileStore` — per-session file storage management
  - Automatic directory creation
  - Save from bytes or file-like objects
  - Session-isolated file listing and lookup
  - Individual and bulk cleanup
  - Total disk usage introspection
- `Silo` — Flask extension tying everything together
  - Header-based session ID (`X-Session-ID`) with query-param fallback
  - `before_request` / `after_request` lifecycle hooks
  - 410 Gone responses for expired sessions on data endpoints
  - Flask factory pattern support (`init_app`)
  - Integrated file-store cleanup on session expiry and reset
- Comprehensive test suite (60+ tests) covering all components
- Type annotations with PEP 561 `py.typed` marker
- Two example applications (counter API, data processor)
