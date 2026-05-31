"""
Workflow Engine — JSON File Store.

JSON-file-backed persistent store for workflows, per-workflow state,
and human-in-the-loop pending actions. No SQLite, no schemas, no migrations —
just JSON files you can inspect with any editor.

Storage layout:
    ~/.hermes/workflow_engine/
    ├── workflows.json          # [{id, name, cron_expression, prompt, ...}, ...]
    ├── pending.json            # [{id, workflow_id, question, status, ...}, ...]
    └── states/
        ├── {workflow_id}.json  # {key: value, ...}  — per-workflow state
        └── ...

Design:
  - Thread-safe via threading.Lock
  - Atomic writes: write to .tmp then os.replace
  - Same API (method names/signatures) as the old SQLite layer so
    tools.py, executor.py, scheduler.py, and plugin_api.py work unchanged
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------


def _get_hermes_home() -> Path:
    val = (os.environ.get("HERMES_HOME") or "").strip()
    return Path(val) if val else Path.home() / ".hermes"


_STORE_DIR = _get_hermes_home() / "workflow_engine"

# ---------------------------------------------------------------------------
# Atomic JSON file I/O
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """Read a JSON file. Returns None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _write_json(path: Path, data: Any) -> None:
    """Atomically write JSON: temp file then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Failed to write %s: %s", path, e)
        raise


# ---------------------------------------------------------------------------
# WorkflowDB — JSON-backed store
# ---------------------------------------------------------------------------


class WorkflowDB:
    """
    JSON-file-backed store for the Workflow Engine.

    Thread-safe via a re-entrant lock. Same public API as the old
    SQLite layer — tools/executor/scheduler/dashboard all work unchanged.
    """

    def __init__(self, store_dir: Optional[Path] = None) -> None:
        base = store_dir or _STORE_DIR
        self._workflows_path = base / "workflows.json"
        self._pending_path = base / "pending.json"
        self._states_dir = base / "states"
        self._lock = threading.Lock()

        self._states_dir.mkdir(parents=True, exist_ok=True)

        # Bootstrap empty files if missing
        with self._lock:
            if not self._workflows_path.exists():
                _write_json(self._workflows_path, [])
            if not self._pending_path.exists():
                _write_json(self._pending_path, [])

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
        origin: Optional[Dict[str, Any]] = None,
        trigger_type: str = "cron",
        deliver: str = "local",
        notify: str = "summary",
    ) -> Dict[str, Any]:
        """Create a new workflow. Raises ValueError if id already exists."""
        now = time.time()

        # Origin is always stored as-is — no silent default.
        # Callers (tools / dashboard) are responsible for providing it
        # or leaving it None when not applicable.

        with self._lock:
            workflows = _read_json(self._workflows_path) or []

            if any(w["id"] == workflow_id for w in workflows):
                raise ValueError(f"Workflow '{workflow_id}' already exists")

            wf = {
                "id": workflow_id,
                "name": name,
                "description": description,
                "cron_expression": cron_expression,
                "prompt": prompt,
                "enabled": True,
                "origin": origin,
                "trigger_type": trigger_type,
                "deliver": deliver,
                "notify": notify,
                "created_at": now,
                "updated_at": now,
            }
            workflows.append(wf)
            _write_json(self._workflows_path, workflows)

        logger.info("Workflow created: id=%s trigger_type=%s deliver=%s", workflow_id, trigger_type, deliver)
        return wf

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get a single workflow by id."""
        with self._lock:
            workflows = _read_json(self._workflows_path) or []
        for w in workflows:
            if w["id"] == workflow_id:
                return w
        return None

    def list_workflows(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """List all workflows, optionally filtering to enabled only."""
        with self._lock:
            workflows = _read_json(self._workflows_path) or []
        if enabled_only:
            workflows = [w for w in workflows if w.get("enabled", True)]
        return sorted(workflows, key=lambda w: w.get("name", ""))

    def update_workflow(
        self,
        workflow_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        cron_expression: Optional[str] = None,
        prompt: Optional[str] = None,
        enabled: Optional[bool] = None,
        origin: Optional[Dict[str, Any]] = None,
        trigger_type: Optional[str] = None,
        deliver: Optional[str] = None,
        notify: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update fields on an existing workflow. Returns updated dict or None."""
        now = time.time()

        with self._lock:
            workflows = _read_json(self._workflows_path) or []
            for w in workflows:
                if w["id"] == workflow_id:
                    if name is not None:
                        w["name"] = name
                    if description is not None:
                        w["description"] = description
                    if cron_expression is not None:
                        w["cron_expression"] = cron_expression
                    if prompt is not None:
                        w["prompt"] = prompt
                    if enabled is not None:
                        w["enabled"] = bool(enabled)
                    if origin is not None:
                        w["origin"] = origin
                    if trigger_type is not None:
                        w["trigger_type"] = trigger_type
                    if deliver is not None:
                        w["deliver"] = deliver
                    if notify is not None:
                        w["notify"] = notify
                    w["updated_at"] = now
                    _write_json(self._workflows_path, workflows)
                    logger.info("Workflow updated: id=%s", workflow_id)
                    return w
        return None

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow, its state file, and its pending actions."""
        with self._lock:
            workflows = _read_json(self._workflows_path) or []
            before = len(workflows)
            workflows = [w for w in workflows if w["id"] != workflow_id]

            if len(workflows) == before:
                return False

            _write_json(self._workflows_path, workflows)

            # Remove pending actions for this workflow
            pending = _read_json(self._pending_path) or []
            pending = [a for a in pending if a["workflow_id"] != workflow_id]
            _write_json(self._pending_path, pending)

            # Remove state file
            state_file = self._states_dir / f"{workflow_id}.json"
            try:
                state_file.unlink(missing_ok=True)
            except OSError:
                pass

        logger.info("Workflow deleted: id=%s", workflow_id)
        return True

    def workflow_exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists."""
        return self.get_workflow(workflow_id) is not None

    # ══════════════════════════════════════════════════════════════════
    # Workflow State (per-workflow JSON files)
    # ══════════════════════════════════════════════════════════════════

    def _state_path(self, workflow_id: str) -> Path:
        return self._states_dir / f"{workflow_id}.json"

    def _read_state(self, workflow_id: str) -> Dict[str, Any]:
        data = _read_json(self._state_path(workflow_id))
        return data if isinstance(data, dict) else {}

    def _write_state(self, workflow_id: str, state: Dict[str, Any]) -> None:
        _write_json(self._state_path(workflow_id), state)

    def save_state(self, workflow_id: str, key: str, value: Any) -> None:
        """Persist a key-value pair scoped to a workflow."""
        with self._lock:
            state = self._read_state(workflow_id)
            state[key] = value
            self._write_state(workflow_id, state)
        logger.debug("State saved: workflow=%s key=%s", workflow_id, key)

    def load_state(self, workflow_id: str, key: str) -> Optional[Any]:
        """Load a state value for a workflow. Returns None if not found."""
        with self._lock:
            state = self._read_state(workflow_id)
        return state.get(key)

    def delete_state(self, workflow_id: str, key: str) -> bool:
        """Delete a state entry. Returns True if it existed."""
        with self._lock:
            state = self._read_state(workflow_id)
            if key not in state:
                return False
            del state[key]
            self._write_state(workflow_id, state)
        return True

    def list_state_keys(self, workflow_id: str) -> List[str]:
        """List all state keys for a workflow."""
        with self._lock:
            state = self._read_state(workflow_id)
        return sorted(state.keys())

    def load_all_state(self, workflow_id: str) -> Dict[str, Any]:
        """Load all state key-value pairs for a workflow as a dict."""
        with self._lock:
            return dict(self._read_state(workflow_id))

    # ══════════════════════════════════════════════════════════════════
    # Workflow Runs (lightweight — tracked by scheduler)
    # ══════════════════════════════════════════════════════════════════

    def create_run(
        self,
        workflow_id: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a lightweight run tracking dict (not persisted)."""
        return {
            "id": run_id or str(uuid.uuid4()),
            "workflow_id": workflow_id,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "pending_action_id": None,
            "error_message": None,
            "output_summary": None,
        }

    def get_active_run(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Check if there's an unresolved pending action (proxy for 'paused')."""
        with self._lock:
            pending = _read_json(self._pending_path) or []
        for a in pending:
            if a["workflow_id"] == workflow_id and a.get("status") == "pending":
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
        max_wait_seconds: Optional[int] = None,
        origin_platform: Optional[str] = None,
        origin_chat_id: Optional[str] = None,
        origin_thread_id: Optional[str] = None,
        origin_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a human-in-the-loop question for a workflow."""
        now = time.time()
        expires_at = (now + max_wait_seconds) if max_wait_seconds else None

        action = {
            "id": str(uuid.uuid4()),
            "workflow_id": workflow_id,
            "run_id": run_id,
            "question": question,
            "context": context,
            "status": "pending",
            "response": None,
            "created_at": now,
            "responded_at": None,
            "expires_at": expires_at,
            "timeout_status": None,
            "origin_platform": origin_platform,
            "origin_chat_id": origin_chat_id,
            "origin_thread_id": origin_thread_id,
            "origin_user_id": origin_user_id,
        }

        with self._lock:
            pending = _read_json(self._pending_path) or []
            pending.append(action)
            _write_json(self._pending_path, pending)

        logger.info("Pending action created: id=%s workflow=%s", action["id"], workflow_id)
        return action

    def get_pending_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Get a single pending action by id."""
        with self._lock:
            pending = _read_json(self._pending_path) or []
        for a in pending:
            if a["id"] == action_id:
                return a
        return None

    def list_pending_actions(
        self,
        workflow_id: Optional[str] = None,
        status: str = "pending",
    ) -> List[Dict[str, Any]]:
        """List pending actions, optionally filtered by workflow_id and status."""
        with self._lock:
            pending = _read_json(self._pending_path) or []

        result = [a for a in pending if a.get("status") == status]
        if workflow_id:
            result = [a for a in result if a["workflow_id"] == workflow_id]

        return sorted(result, key=lambda a: a.get("created_at", 0), reverse=True)

    def resolve_pending_action(
        self, action_id: str, response: str
    ) -> Optional[Dict[str, Any]]:
        """Record a human response to a pending action."""
        now = time.time()

        with self._lock:
            pending = _read_json(self._pending_path) or []
            for a in pending:
                if a["id"] == action_id and a.get("status") == "pending":
                    a["status"] = "resolved"
                    a["response"] = response
                    a["responded_at"] = now
                    _write_json(self._pending_path, pending)
                    logger.info("Pending action resolved: id=%s", action_id)
                    return a
        return None

    def dismiss_pending_action(self, action_id: str) -> bool:
        """Dismiss a pending action without a response."""
        with self._lock:
            pending = _read_json(self._pending_path) or []
            for a in pending:
                if a["id"] == action_id and a.get("status") == "pending":
                    a["status"] = "dismissed"
                    a["responded_at"] = time.time()
                    _write_json(self._pending_path, pending)
                    return True
        return False

    def delete_pending_action(self, action_id: str) -> bool:
        """Hard-delete a pending action."""
        with self._lock:
            pending = _read_json(self._pending_path) or []
            before = len(pending)
            pending = [a for a in pending if a["id"] != action_id]
            if len(pending) == before:
                return False
            _write_json(self._pending_path, pending)
        return True

    def get_unacknowledged_responses(
        self, workflow_id: str
    ) -> List[Dict[str, Any]]:
        """Get resolved answers the agent has not consumed yet.

        A response is "acknowledged" once a run has been built with it in
        context (see :meth:`acknowledge_responses`). This guarantees a human
        answer is surfaced to the agent exactly once — so a resumed run acts on
        it instead of re-asking, and later scheduled runs don't replay stale
        answers.
        """
        with self._lock:
            pending = _read_json(self._pending_path) or []
        result = [
            a for a in pending
            if a["workflow_id"] == workflow_id
            and a.get("status") == "resolved"
            and not a.get("acknowledged")
        ]
        return sorted(result, key=lambda a: a.get("responded_at", 0))

    def acknowledge_responses(self, action_ids: List[str]) -> None:
        """Mark the given resolved actions as consumed by a run."""
        if not action_ids:
            return
        wanted = set(action_ids)
        with self._lock:
            pending = _read_json(self._pending_path) or []
            changed = False
            for a in pending:
                if a["id"] in wanted and not a.get("acknowledged"):
                    a["acknowledged"] = True
                    changed = True
            if changed:
                _write_json(self._pending_path, pending)

    def get_expired_actions(self) -> List[Dict[str, Any]]:
        """Get all pending actions that have passed their expires_at timestamp."""
        now = time.time()
        with self._lock:
            pending = _read_json(self._pending_path) or []
        return [
            a for a in pending
            if a.get("status") == "pending"
            and a.get("expires_at") is not None
            and a["expires_at"] < now
        ]

    def expire_action(self, action_id: str) -> Optional[Dict[str, Any]]:
        """Mark a pending action as timed-out (no answer within the limit).

        Recorded as ``status="resolved"`` with ``timeout_status="expired"`` so
        it flows through the same unacknowledged-response path as a real answer
        — the resumed run is told the question timed out and proceeds without
        input, instead of re-asking. (This is distinct from a user *dismissal*,
        which stays ``status="dismissed"`` and is never surfaced as an answer.)
        """
        now = time.time()
        with self._lock:
            pending = _read_json(self._pending_path) or []
            for a in pending:
                if a["id"] == action_id and a.get("status") == "pending":
                    a["status"] = "resolved"
                    a["timeout_status"] = "expired"
                    a["responded_at"] = now
                    _write_json(self._pending_path, pending)
                    logger.info("Pending action timed out: id=%s", action_id)
                    return a
        return None

    # ══════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════

    def close(self) -> None:
        """No-op for JSON store — kept for API compatibility."""
        pass

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
