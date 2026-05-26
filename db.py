"""
Workflow Engine Database Layer.

Provides a lightweight SQLite-backed persistent store for workflow state
and pending human-in-the-loop actions. Operates independently of Hermes
core's state.db to avoid coupling.

Design follows Hermes's own SessionDB patterns:
- WAL mode for concurrent reads
- BEGIN IMMEDIATE with jitter retry for write contention
- sqlite3.Row factory for dict-like row access
- Thread-safe via threading.Lock

Database: ~/.hermes/workflow_engine.db
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Database location — resolves ~/.hermes dynamically
# ---------------------------------------------------------------------------


def _get_hermes_home() -> Path:
    """Resolve Hermes home directory."""
    import os
    val = (os.environ.get("HERMES_HOME") or "").strip()
    return Path(val) if val else Path.home() / ".hermes"


DEFAULT_DB_PATH = _get_hermes_home() / "workflow_engine.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflow_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_actions (
    workflow_id   TEXT PRIMARY KEY,
    cron_job_id   TEXT,
    question      TEXT NOT NULL,
    context       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    REAL NOT NULL,
    response      TEXT,
    responded_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_pending_status
    ON pending_actions(status);

CREATE INDEX IF NOT EXISTS idx_pending_cron_job
    ON pending_actions(cron_job_id);
"""


# ---------------------------------------------------------------------------
# WorkflowDB
# ---------------------------------------------------------------------------

class WorkflowDB:
    """
    SQLite-backed store for workflow state and pending human interactions.

    Thread-safe: uses a re-entrant lock + jitter-retry write pattern.
    """

    # Write-contention tuning (mirrors Hermes SessionDB)
    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020   # 20 ms
    _WRITE_RETRY_MAX_S = 0.150   # 150 ms

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=1.0,
            isolation_level=None,  # we manage transactions ourselves
        )
        self._conn.row_factory = sqlite3.Row

        # Enable WAL mode with fallback
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.warning("WAL mode unavailable for workflow_engine.db; using default journal")

        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ── Schema init ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)

    # ── Write helper ─────────────────────────────────────────────────────

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute a write transaction with BEGIN IMMEDIATE and jitter retry."""
        last_err: Optional[Exception] = None
        for _attempt in range(self._WRITE_MAX_RETRIES):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                        self._conn.commit()
                        return result
                    except BaseException:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
            except sqlite3.OperationalError as e:
                last_err = e
                jitter = random.uniform(self._WRITE_RETRY_MIN_S, self._WRITE_RETRY_MAX_S)
                time.sleep(jitter)
        raise last_err  # type: ignore[misc]

    # ── State CRUD ───────────────────────────────────────────────────────

    def save_state(self, key: str, value: Any) -> None:
        """
        Persist a workflow state value under *key*.

        *value* is JSON-serialized before storage. Overwrites any
        existing value for the same key.
        """
        now = time.time()
        serialized = json.dumps(value)

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO workflow_state (key, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, serialized, now, now),
            )
        self._execute_write(_do)
        logger.debug("Workflow state saved: key=%s", key)

    def load_state(self, key: str) -> Optional[Any]:
        """Load a workflow state value by *key*. Returns None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM workflow_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupted state for key=%s, returning raw string", key)
            return row["value"]

    def delete_state(self, key: str) -> bool:
        """Delete a workflow state entry. Returns True if it existed."""

        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM workflow_state WHERE key = ?", (key,)
            )
            return cursor.rowcount > 0
        return self._execute_write(_do)

    def list_state_keys(self, prefix: Optional[str] = None) -> List[str]:
        """List all state keys, optionally filtered by prefix."""
        with self._lock:
            if prefix:
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = self._conn.execute(
                    "SELECT key FROM workflow_state WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                    (f"{escaped}%",),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT key FROM workflow_state ORDER BY key"
                ).fetchall()
        return [r["key"] for r in rows]

    # ── Pending Actions ──────────────────────────────────────────────────

    def create_pending_action(
        self,
        workflow_id: str,
        question: str,
        cron_job_id: Optional[str] = None,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a workflow that needs human input before continuing.

        Returns the created action dict.
        """
        now = time.time()
        action = {
            "workflow_id": workflow_id,
            "cron_job_id": cron_job_id,
            "question": question,
            "context": context,
            "status": "pending",
            "created_at": now,
            "response": None,
            "responded_at": None,
        }

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO pending_actions
                   (workflow_id, cron_job_id, question, context, status, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?)
                   ON CONFLICT(workflow_id) DO UPDATE SET
                       question = excluded.question,
                       context = excluded.context,
                       status = 'pending',
                       created_at = excluded.created_at,
                       response = NULL,
                       responded_at = NULL""",
                (workflow_id, cron_job_id, question, context, now),
            )
        self._execute_write(_do)
        logger.info("Pending action created: workflow_id=%s", workflow_id)
        return action

    def get_pending_action(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get a single pending action by workflow_id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pending_actions WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending_actions(
        self, status: Optional[str] = "pending", cron_job_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List pending actions, optionally filtered by status and cron_job_id."""
        with self._lock:
            if cron_job_id and status:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions WHERE status = ? AND cron_job_id = ? ORDER BY created_at DESC",
                    (status, cron_job_id),
                ).fetchall()
            elif status:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            elif cron_job_id:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions WHERE cron_job_id = ? ORDER BY created_at DESC",
                    (cron_job_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def resolve_pending_action(
        self, workflow_id: str, response: str
    ) -> Optional[Dict[str, Any]]:
        """
        Record a human response to a pending workflow.

        Returns the updated action dict, or None if not found.
        """
        now = time.time()

        def _do(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            cursor = conn.execute(
                "SELECT * FROM pending_actions WHERE workflow_id = ?", (workflow_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE pending_actions
                   SET status = 'resolved', response = ?, responded_at = ?
                   WHERE workflow_id = ?""",
                (response, now, workflow_id),
            )
            return dict(row) | {"status": "resolved", "response": response, "responded_at": now}

        result = self._execute_write(_do)
        if result:
            logger.info("Pending action resolved: workflow_id=%s", workflow_id)
        return result

    def delete_pending_action(self, workflow_id: str) -> bool:
        """Delete a pending action entirely."""
        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM pending_actions WHERE workflow_id = ?", (workflow_id,)
            )
            return cursor.rowcount > 0
        return self._execute_write(_do)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except Exception:
                    pass
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "WorkflowDB":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_db: Optional[WorkflowDB] = None
_db_lock = threading.Lock()


def get_db() -> WorkflowDB:
    """Get or create the module-level WorkflowDB singleton."""
    global _db
    if _db is not None:
        return _db
    with _db_lock:
        if _db is None:
            _db = WorkflowDB()
        return _db
