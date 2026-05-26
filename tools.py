"""
Tool handlers for the Workflow Engine plugin.

Each handler receives args (dict) from the LLM and returns a JSON string.
Follows Hermes conventions:
- Signature: def handler(args: dict, **kwargs) -> str
- Always returns a JSON string, even on error
- Never raises exceptions — catch and return error JSON
- Accept **kwargs for forward compatibility
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .db import get_db


# ── workflow_save_state ───────────────────────────────────────────────────

def _handle_save_state(args: Dict[str, Any], **kwargs: Any) -> str:
    """Persist a workflow state value."""
    key = (args.get("key") or "").strip()
    value_raw = args.get("value")

    if not key:
        return json.dumps({"error": "key is required"})

    if value_raw is None:
        return json.dumps({"error": "value is required"})

    # value arrives as a string from the LLM. Attempt to parse as JSON
    # so we store structured data; fall back to raw string.
    try:
        if isinstance(value_raw, str):
            parsed = json.loads(value_raw)
        else:
            parsed = value_raw
    except (json.JSONDecodeError, TypeError):
        parsed = value_raw

    try:
        db = get_db()
        db.save_state(key, parsed)
        return json.dumps({
            "success": True,
            "key": key,
            "message": f"State saved for key '{key}'.",
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to save state: {e}"})


# ── workflow_load_state ───────────────────────────────────────────────────

def _handle_load_state(args: Dict[str, Any], **kwargs: Any) -> str:
    """Retrieve a workflow state value."""
    key = (args.get("key") or "").strip()

    if not key:
        return json.dumps({"error": "key is required"})

    try:
        db = get_db()
        value = db.load_state(key)
        if value is None:
            return json.dumps({
                "found": False,
                "key": key,
                "value": None,
                "message": f"No state found for key '{key}'. Initialize defaults as needed.",
            })
        return json.dumps({
            "found": True,
            "key": key,
            "value": value,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to load state: {e}"})


# ── workflow_wait_for_user ────────────────────────────────────────────────

def _handle_wait_for_user(args: Dict[str, Any], **kwargs: Any) -> str:
    """Pause workflow and wait for human input."""
    workflow_id = (args.get("workflow_id") or "").strip()
    question = (args.get("question") or "").strip()
    cron_job_id = (args.get("cron_job_id") or "").strip() or None
    context = (args.get("context") or "").strip() or None

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})
    if not question:
        return json.dumps({"error": "question is required"})

    try:
        db = get_db()
        action = db.create_pending_action(
            workflow_id=workflow_id,
            question=question,
            cron_job_id=cron_job_id,
            context=context,
        )
        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "status": "pending",
            "message": (
                f"Workflow '{workflow_id}' is now waiting for human input. "
                f"The user can respond with: workflow_submit_response('{workflow_id}', '<their answer>') "
                f"or by using the /workflows command. "
                f"This cron job should now END — the workflow will resume on "
                f"a future run once the human responds."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to pause workflow: {e}"})


# ── workflow_submit_response ──────────────────────────────────────────────

def _handle_submit_response(args: Dict[str, Any], **kwargs: Any) -> str:
    """Submit a human response to a pending workflow."""
    workflow_id = (args.get("workflow_id") or "").strip()
    response = (args.get("response") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})
    if not response:
        return json.dumps({"error": "response is required"})

    try:
        db = get_db()
        resolved = db.resolve_pending_action(workflow_id, response)
        if resolved is None:
            return json.dumps({
                "success": False,
                "workflow_id": workflow_id,
                "message": f"No pending workflow found with ID '{workflow_id}'. It may have already been resolved.",
            })
        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "status": "resolved",
            "response": response,
            "message": (
                f"Response submitted for workflow '{workflow_id}'. "
                f"The agent will see this response on its next cron execution."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to submit response: {e}"})


# ── workflow_list_pending ─────────────────────────────────────────────────

def _handle_list_pending(args: Dict[str, Any], **kwargs: Any) -> str:
    """List workflows waiting for human input."""
    cron_job_id = (args.get("cron_job_id") or "").strip() or None

    try:
        db = get_db()
        actions = db.list_pending_actions(status="pending", cron_job_id=cron_job_id)

        if not actions:
            return json.dumps({
                "pending_count": 0,
                "pending_workflows": [],
                "message": "No pending workflows awaiting human input.",
            })

        # Format for LLM readability
        formatted = []
        for a in actions:
            formatted.append({
                "workflow_id": a["workflow_id"],
                "question": a["question"],
                "context": a.get("context"),
                "cron_job_id": a.get("cron_job_id"),
                "created_at": a["created_at"],
            })

        return json.dumps({
            "pending_count": len(formatted),
            "pending_workflows": formatted,
            "message": (
                f"Found {len(formatted)} pending workflow(s). "
                f"To respond to one, use workflow_submit_response(workflow_id, response). "
                f"The agent should check these and incorporate any human responses "
                f"into the current run."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to list pending workflows: {e}"})


# ── Handler map ───────────────────────────────────────────────────────────

HANDLER_MAP = {
    "workflow_save_state": _handle_save_state,
    "workflow_load_state": _handle_load_state,
    "workflow_wait_for_user": _handle_wait_for_user,
    "workflow_submit_response": _handle_submit_response,
    "workflow_list_pending": _handle_list_pending,
}
