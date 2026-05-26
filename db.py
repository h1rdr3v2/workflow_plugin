"""
Workflow Engine Database Layer.

SQLite-backed persistent store for workflows, per-workflow state,
and human-in-the-loop pending actions.

Database: ~/.hermes/workflow_engine.db
Tables:
  - workflows       — workflow definitions (id, name, cron, prompt, enabled)
  - workflow_state  — per-workflow key-value store (workflow_id, key, value)
  - pending_actions — human-in-the-loop requests (id, workflow_id, question, status)

Design:
  - WAL mode for concurrent reads
  - BEGIN IMMEDIATE with jitter retry for write contention
  - sqlite3.Row factory for dict-like row access
  - Thread-safe via threading.Lock
  - All CRUD operations return dicts or dataclass-compatible rows
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Database location
# ---------------------------------------------------------------------------


def _get_hermes_home() -> Path:
    val = (os.environ.get("HERMES_HOME") or "").strip()
    return Path(val) if val else Path.home() / ".hermes"


DEFAULT_DB_PATH = _get_hermes_home() / "workflow_engine.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflows (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    cron_expression TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_state (
    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (workflow_id, key)
);

CREATE TABLE IF NOT EXISTS pending_actions (
    id           TEXT PRIMARY KEY,
    workflow_id  TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    run_id       TEXT,
    question     TEXT NOT NULL,
    context      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    response     TEXT,
    created_at   REAL NOT NULL,
    responded_at REAL
);

CREATE INDEX IF NOT EXISTS idx_workflow_state_wf
    ON workflow_state(workflow_id);

CREATE INDEX IF NOT EXISTS idx_pending_workflow
    ON pending_actions(workflow_id);

CREATE INDEX IF NOT EXISTS idx_pending_status
    ON pending_actions(status);

CREATE INDEX IF NOT EXISTS idx_pending_run
    ON pending_actions(run_id);
"""


# ---------------------------------------------------------------------------
# WorkflowDB
# ---------------------------------------------------------------------------


class WorkflowDB:
    """
    SQLite-backed store for the Workflow Engine.

    Thread-safe: re-entrant lock + jitter-retry write pattern.
    """

    _WRITE_MAX_RETRIES = 15
    _WRITE_RETRY_MIN_S = 0.020
    _WRITE_RETRY_MAX_S = 0.150

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=1.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row

        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            logger.warning("WAL mode unavailable; using default journal")

        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ── Schema init ──────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create tables. Migrates from v0.1.x schema if needed."""
        with self._lock:
            # Detect if we need migration: the 'workflows' table is new in v1.
            # If it doesn't exist, we may have old v0.1.x tables that need
            # to be dropped before the new schema can be applied.
            try:
                self._conn.execute("SELECT 1 FROM workflows LIMIT 0")
            except sqlite3.OperationalError:
                # 'workflows' table doesn't exist — we're either fresh or
                # running against a v0.1.x database. Drop old tables so
                # CREATE TABLE IF NOT EXISTS doesn't leave stale schemas.
                logger.info("Migrating from v0.1.x schema — dropping old tables")
                self._conn.execute("DROP TABLE IF EXISTS workflow_state")
                self._conn.execute("DROP TABLE IF EXISTS pending_actions")
                # Also drop any old indexes that may linger
                self._conn.execute("DROP INDEX IF EXISTS idx_pending_status")
                self._conn.execute("DROP INDEX IF EXISTS idx_pending_cron_job")

            self._conn.executescript(SCHEMA_SQL)

    # ── Write helper ─────────────────────────────────────────────────

    def _execute_write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
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

    # ══════════════════════════════════════════════════════════════════
    # Workflow CRUD
    # ══════════════════════════════════════════════════════════════════

    def create_workflow(
        self,
        workflow_id: str,
        name: str,
        cron_expression: str,
        prompt: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a new workflow. Raises ValueError if id already exists."""
        now = time.time()

        def _do(conn: sqlite3.Connection) -> Dict[str, Any]:
            try:
                conn.execute(
                    """INSERT INTO workflows (id, name, description, cron_expression, prompt, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                    (workflow_id, name, description, cron_expression, prompt, now, now),
                )
            except sqlite3.IntegrityError:
                raise ValueError(f"Workflow '{workflow_id}' already exists")
            return {
                "id": workflow_id,
                "name": name,
                "description": description,
                "cron_expression": cron_expression,
                "prompt": prompt,
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }

        result = self._execute_write(_do)
        logger.info("Workflow created: id=%s", workflow_id)
        return result

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get a single workflow by id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_workflows(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """List all workflows, optionally filtering to enabled only."""
        with self._lock:
            if enabled_only:
                rows = self._conn.execute(
                    "SELECT * FROM workflows WHERE enabled = 1 ORDER BY name"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM workflows ORDER BY name"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_workflow(
        self,
        workflow_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cron_expression: Optional[str] = None,
        prompt: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update fields on an existing workflow. Returns updated row or None."""
        now = time.time()

        def _do(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            existing = conn.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
            if existing is None:
                return None

            updates: List[str] = []
            params: List[Any] = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if cron_expression is not None:
                updates.append("cron_expression = ?")
                params.append(cron_expression)
            if prompt is not None:
                updates.append("prompt = ?")
                params.append(prompt)
            if enabled is not None:
                updates.append("enabled = ?")
                params.append(1 if enabled else 0)

            if not updates:
                return dict(existing)

            updates.append("updated_at = ?")
            params.append(now)
            params.append(workflow_id)

            conn.execute(
                f"UPDATE workflows SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            # Return the full updated row
            updated = conn.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
            return dict(updated) if updated else None

        result = self._execute_write(_do)
        if result:
            logger.info("Workflow updated: id=%s", workflow_id)
        return result

    def delete_workflow(self, workflow_id: str) -> bool:
        """
        Delete a workflow and all its state + pending actions (CASCADE).
        Returns True if the workflow existed.
        """

        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            return cursor.rowcount > 0

        result = self._execute_write(_do)
        if result:
            logger.info("Workflow deleted: id=%s", workflow_id)
        return result

    def workflow_exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        return row is not None

    # ══════════════════════════════════════════════════════════════════
    # Workflow State (per-workflow key-value)
    # ══════════════════════════════════════════════════════════════════

    def save_state(self, workflow_id: str, key: str, value: Any) -> None:
        """Persist a key-value pair scoped to a workflow. JSON-serialized."""
        now = time.time()
        serialized = json.dumps(value)

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO workflow_state (workflow_id, key, value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(workflow_id, key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (workflow_id, key, serialized, now),
            )

        self._execute_write(_do)
        logger.debug("State saved: workflow=%s key=%s", workflow_id, key)

    def load_state(self, workflow_id: str, key: str) -> Optional[Any]:
        """Load a state value for a workflow. Returns None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM workflow_state WHERE workflow_id = ? AND key = ?",
                (workflow_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Corrupted state: workflow=%s key=%s", workflow_id, key)
            return row["value"]

    def delete_state(self, workflow_id: str, key: str) -> bool:
        """Delete a state entry. Returns True if it existed."""

        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                "DELETE FROM workflow_state WHERE workflow_id = ? AND key = ?",
                (workflow_id, key),
            )
            return cursor.rowcount > 0

        return self._execute_write(_do)

    def list_state_keys(self, workflow_id: str) -> List[str]:
        """List all state keys for a workflow."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM workflow_state WHERE workflow_id = ? ORDER BY key",
                (workflow_id,),
            ).fetchall()
        return [r["key"] for r in rows]

    def load_all_state(self, workflow_id: str) -> Dict[str, Any]:
        """Load all state key-value pairs for a workflow as a dict."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM workflow_state WHERE workflow_id = ? ORDER BY key",
                (workflow_id,),
            ).fetchall()
        result: Dict[str, Any] = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                result[r["key"]] = r["value"]
        return result

    # ══════════════════════════════════════════════════════════════════
    # Workflow Runs
    # ══════════════════════════════════════════════════════════════════

    def create_run(
        self,
        workflow_id: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record the start of a workflow execution.
        Returns the run dict. Does NOT persist to a table — runs are
        tracked in-memory via the scheduler. This method exists as a
        future hook for persisting run history.

        For now, returns a lightweight run tracking dict.
        """
        rid = run_id or str(uuid.uuid4())
        now = time.time()
        return {
            "id": rid,
            "workflow_id": workflow_id,
            "status": "running",
            "started_at": now,
            "finished_at": None,
            "pending_action_id": None,
            "error_message": None,
            "output_summary": None,
        }

    def get_active_run(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if there's an active (running/paused) run for a workflow.
        Used by the scheduler to prevent overlapping executions.

        Since runs are not persisted to a table yet, we check pending_actions
        for unresolved actions tied to this workflow as a proxy for "paused" state.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT 1 FROM pending_actions
                   WHERE workflow_id = ? AND status = 'pending'
                   LIMIT 1""",
                (workflow_id,),
            ).fetchone()
        # If there's a pending action, the workflow is effectively "paused"
        if row:
            return {
                "id": "paused",
                "workflow_id": workflow_id,
                "status": "paused",
                "started_at": 0.0,
            }
        return None

    # ══════════════════════════════════════════════════════════════════
    # Pending Actions (human-in-the-loop)
    # ══════════════════════════════════════════════════════════════════

    def create_pending_action(
        self,
        workflow_id: str,
        question: str,
        run_id: Optional[str] = None,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a human-in-the-loop question for a workflow."""
        now = time.time()
        action_id = str(uuid.uuid4())

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                """INSERT INTO pending_actions
                   (id, workflow_id, run_id, question, context, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (action_id, workflow_id, run_id, question, context, now),
            )

        self._execute_write(_do)
        logger.info("Pending action created: id=%s workflow=%s", action_id, workflow_id)
        return {
            "id": action_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "question": question,
            "context": context,
            "status": "pending",
            "response": None,
            "created_at": now,
            "responded_at": None,
        }

    def get_pending_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Get a single pending action by id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_pending_actions(
        self,
        workflow_id: Optional[str] = None,
        status: str = "pending",
    ) -> List[Dict[str, Any]]:
        """List pending actions, optionally filtered by workflow_id and status."""
        with self._lock:
            if workflow_id:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions WHERE workflow_id = ? AND status = ? ORDER BY created_at DESC",
                    (workflow_id, status),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM pending_actions WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
        return [dict(r) for r in rows]

    def resolve_pending_action(
        self, action_id: str, response: str
    ) -> Optional[Dict[str, Any]]:
        """Record a human response to a pending action."""
        now = time.time()

        def _do(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            existing = conn.execute(
                "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if existing is None:
                return None
            if existing["status"] != "pending":
                return None
            conn.execute(
                """UPDATE pending_actions
                   SET status = 'resolved', response = ?, responded_at = ?
                   WHERE id = ?""",
                (response, now, action_id),
            )
            return dict(existing) | {
                "status": "resolved",
                "response": response,
                "responded_at": now,
            }

        result = self._execute_write(_do)
        if result:
            logger.info("Pending action resolved: id=%s", action_id)
        return result

    def dismiss_pending_action(self, action_id: str) -> bool:
        """Dismiss a pending action without a response."""

        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                """UPDATE pending_actions SET status = 'dismissed', responded_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (time.time(), action_id),
            )
            return cursor.rowcount > 0

        return self._execute_write(_do)

    def delete_pending_action(self, action_id: str) -> bool:
        """Hard-delete a pending action."""

        def _do(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute("DELETE FROM pending_actions WHERE id = ?", (action_id,))
            return cursor.rowcount > 0

        return self._execute_write(_do)

    def get_resolved_since(
        self, workflow_id: str, since: float
    ) -> List[Dict[str, Any]]:
        """
        Get pending actions that were resolved since a given timestamp.
        Used by the executor to inject recent human responses into the
        next workflow run.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM pending_actions
                   WHERE workflow_id = ? AND status = 'resolved' AND responded_at > ?
                   ORDER BY responded_at ASC""",
                (workflow_id, since),
            ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════

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
# Module-level singleton
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
