"""
Workflow Engine — Tool Handlers.

Each handler receives args (dict) and optional keyword arguments from
the Hermes tool dispatch layer, and returns a JSON string.

Conventions:
- Signature: def handler(args: dict, **kwargs) -> str
- Always returns a JSON string, even on error
- Never raises exceptions — catch and return error JSON
- The **kwargs parameter captures any future Hermes additions and
  may include 'workflow_id' to identify the current execution context
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from .db import get_db
from .scheduler import schedule_workflow, unschedule_workflow

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_current_workflow_id(kwargs: Dict[str, Any]) -> str | None:
    """
    Extract the workflow_id from the execution context.
    
    In a workflow run, kwargs includes the workflow_id of the currently
    executing workflow. For tools called outside a workflow run (e.g.,
    create, list), this returns None.
    """
    return (kwargs.get("workflow_id") or "").strip() or None


# ═══════════════════════════════════════════════════════════════════════════
# 1. workflow_create
# ═══════════════════════════════════════════════════════════════════════════

def _handle_create(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()
    name = (args.get("name") or "").strip()
    cron_expression = (args.get("cron_expression") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    description = (args.get("description") or "").strip()

    # Validation
    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})
    if not name:
        return json.dumps({"error": "name is required"})
    if not cron_expression:
        return json.dumps({"error": "cron_expression is required"})
    if not prompt:
        return json.dumps({"error": "prompt is required"})

    # Basic cron validation — 5 fields
    parts = cron_expression.split()
    if len(parts) != 5:
        return json.dumps({
            "error": f"cron_expression must have exactly 5 fields, got {len(parts)}. Example: '0 9 * * 1-5'"
        })

    # Real cron validation via APScheduler
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron_expression)
    except (ValueError, KeyError) as e:
        return json.dumps({
            "error": f"Invalid cron expression '{cron_expression}': {e}"
        })
    except ImportError:
        pass  # APScheduler not available — skip deep validation

    try:
        db = get_db()
        wf = db.create_workflow(workflow_id, name, cron_expression, prompt, description)

        # Schedule it immediately
        schedule_workflow(wf)

        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "name": name,
            "cron_expression": cron_expression,
            "message": f"Workflow '{name}' created and scheduled with cron '{cron_expression}'. It will run automatically on schedule.",
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception("Failed to create workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to create workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 2. workflow_update
# ═══════════════════════════════════════════════════════════════════════════

def _handle_update(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    try:
        db = get_db()

        # Build kwargs for update, only passing provided fields
        update_kwargs: Dict[str, Any] = {}
        for field in ("name", "description", "cron_expression", "prompt"):
            if field in args and args[field] is not None:
                val = (args[field] or "").strip()
                # Validate cron if being updated
                if field == "cron_expression" and val:
                    parts = val.split()
                    if len(parts) != 5:
                        return json.dumps({"error": f"cron_expression must have exactly 5 fields, got {len(parts)}"})
                    try:
                        from apscheduler.triggers.cron import CronTrigger
                        CronTrigger.from_crontab(val)
                    except (ValueError, KeyError) as e:
                        return json.dumps({"error": f"Invalid cron expression '{val}': {e}"})
                    except ImportError:
                        pass
                update_kwargs[field] = val

        if "enabled" in args and args["enabled"] is not None:
            update_kwargs["enabled"] = bool(args["enabled"])

        result = db.update_workflow(workflow_id, **update_kwargs)

        if result is None:
            return json.dumps({
                "success": False,
                "message": f"Workflow '{workflow_id}' not found.",
            })

        # Re-schedule to pick up cron/enabled changes
        schedule_workflow(result)

        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "updated_fields": list(update_kwargs.keys()),
            "message": f"Workflow '{workflow_id}' updated.",
        })
    except Exception as e:
        logger.exception("Failed to update workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to update workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 3. workflow_delete
# ═══════════════════════════════════════════════════════════════════════════

def _handle_delete(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    try:
        db = get_db()
        existed = db.delete_workflow(workflow_id)

        if existed:
            unschedule_workflow(workflow_id)
            return json.dumps({
                "success": True,
                "message": f"Workflow '{workflow_id}' deleted permanently, including all state and pending actions.",
            })
        else:
            return json.dumps({
                "success": False,
                "message": f"Workflow '{workflow_id}' not found.",
            })
    except Exception as e:
        logger.exception("Failed to delete workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to delete workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 4. workflow_list
# ═══════════════════════════════════════════════════════════════════════════

def _handle_list(args: Dict[str, Any], **kwargs: Any) -> str:
    enabled_only = bool(args.get("enabled_only", False))

    try:
        db = get_db()
        workflows = db.list_workflows(enabled_only=enabled_only)

        if not workflows:
            return json.dumps({
                "workflows": [],
                "count": 0,
                "message": "No workflows found. Create one with workflow_create().",
            })

        # Strip prompt for brevity in list view
        summary = []
        for wf in workflows:
            summary.append({
                "workflow_id": wf["id"],
                "name": wf["name"],
                "description": wf.get("description", ""),
                "cron_expression": wf["cron_expression"],
                "enabled": bool(wf["enabled"]),
                "created_at": wf["created_at"],
            })

        return json.dumps({
            "workflows": summary,
            "count": len(summary),
            "message": f"Found {len(summary)} workflow(s).",
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to list workflows: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 5. workflow_get
# ═══════════════════════════════════════════════════════════════════════════

def _handle_get(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    try:
        db = get_db()
        wf = db.get_workflow(workflow_id)

        if wf is None:
            return json.dumps({
                "found": False,
                "message": f"Workflow '{workflow_id}' not found.",
            })

        # Gather state keys and pending actions
        state_keys = db.list_state_keys(workflow_id)
        pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")

        return json.dumps({
            "found": True,
            "workflow": {
                "id": wf["id"],
                "name": wf["name"],
                "description": wf.get("description", ""),
                "cron_expression": wf["cron_expression"],
                "prompt": wf["prompt"],
                "enabled": bool(wf["enabled"]),
                "created_at": wf["created_at"],
                "updated_at": wf["updated_at"],
            },
            "state_keys": state_keys,
            "state_count": len(state_keys),
            "pending_actions": [
                {
                    "action_id": pa["id"],
                    "question": pa["question"],
                    "status": pa["status"],
                    "created_at": pa["created_at"],
                }
                for pa in pending
            ],
            "pending_count": len(pending),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to get workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 6. workflow_save_state
# ═══════════════════════════════════════════════════════════════════════════

def _handle_save_state(args: Dict[str, Any], **kwargs: Any) -> str:
    key = (args.get("key") or "").strip()
    value_raw = args.get("value")

    if not key:
        return json.dumps({"error": "key is required"})

    if value_raw is None:
        return json.dumps({"error": "value is required"})

    # Get current workflow from context
    current_wf = _get_current_workflow_id(kwargs)
    if not current_wf:
        return json.dumps({
            "error": "No workflow context available. workflow_save_state can only be called during a workflow run."
        })

    # Parse value: try JSON, fall back to raw string
    try:
        if isinstance(value_raw, str):
            parsed = json.loads(value_raw)
        else:
            parsed = value_raw
    except (json.JSONDecodeError, TypeError):
        parsed = value_raw

    try:
        db = get_db()
        db.save_state(current_wf, key, parsed)
        return json.dumps({
            "success": True,
            "workflow_id": current_wf,
            "key": key,
            "message": f"State '{key}' saved for workflow '{current_wf}'.",
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to save state: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 7. workflow_load_state
# ═══════════════════════════════════════════════════════════════════════════

def _handle_load_state(args: Dict[str, Any], **kwargs: Any) -> str:
    key = (args.get("key") or "").strip()

    if not key:
        return json.dumps({"error": "key is required"})

    current_wf = _get_current_workflow_id(kwargs)
    if not current_wf:
        return json.dumps({
            "error": "No workflow context available. workflow_load_state can only be called during a workflow run."
        })

    try:
        db = get_db()
        value = db.load_state(current_wf, key)
        if value is None:
            return json.dumps({
                "found": False,
                "workflow_id": current_wf,
                "key": key,
                "value": None,
                "message": f"No state found for key '{key}' in workflow '{current_wf}'. Initialize defaults as needed.",
            })
        return json.dumps({
            "found": True,
            "workflow_id": current_wf,
            "key": key,
            "value": value,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to load state: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 8. workflow_delete_state
# ═══════════════════════════════════════════════════════════════════════════

def _handle_delete_state(args: Dict[str, Any], **kwargs: Any) -> str:
    key = (args.get("key") or "").strip()

    if not key:
        return json.dumps({"error": "key is required"})

    current_wf = _get_current_workflow_id(kwargs)
    if not current_wf:
        return json.dumps({
            "error": "No workflow context available. workflow_delete_state can only be called during a workflow run."
        })

    try:
        db = get_db()
        existed = db.delete_state(current_wf, key)
        return json.dumps({
            "success": True,
            "deleted": existed,
            "workflow_id": current_wf,
            "key": key,
            "message": f"State key '{key}' {'deleted' if existed else 'was not found'}.",
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to delete state: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 9. workflow_wait_for_user
# ═══════════════════════════════════════════════════════════════════════════

def _handle_wait_for_user(args: Dict[str, Any], **kwargs: Any) -> str:
    question = (args.get("question") or "").strip()
    context = (args.get("context") or "").strip() or None
    max_wait_seconds = args.get("max_wait_seconds")  # optional int timeout

    if not question:
        return json.dumps({"error": "question is required"})

    # Validate max_wait_seconds
    if max_wait_seconds is not None:
        try:
            max_wait_seconds = int(max_wait_seconds)
            if max_wait_seconds <= 0:
                return json.dumps({"error": "max_wait_seconds must be a positive integer"})
        except (ValueError, TypeError):
            return json.dumps({"error": "max_wait_seconds must be an integer"})

    current_wf = _get_current_workflow_id(kwargs)
    if not current_wf:
        return json.dumps({
            "error": "No workflow context available. workflow_wait_for_user can only be called during a workflow run."
        })

    try:
        db = get_db()
        # Extract origin from kwargs (set by executor/__init__)
        origin_platform = (kwargs.get("origin_platform") or "").strip() or None
        origin_chat_id = (kwargs.get("origin_chat_id") or "").strip() or None
        origin_thread_id = (kwargs.get("origin_thread_id") or "").strip() or None
        origin_user_id = (kwargs.get("origin_user_id") or "").strip() or None

        action = db.create_pending_action(
            workflow_id=current_wf,
            question=question,
            context=context,
            max_wait_seconds=max_wait_seconds,
            origin_platform=origin_platform,
            origin_chat_id=origin_chat_id,
            origin_thread_id=origin_thread_id,
            origin_user_id=origin_user_id,
        )

        result = {
            "success": True,
            "action_id": action["id"],
            "workflow_id": current_wf,
            "status": "pending",
            "message": (
                f"Workflow '{current_wf}' is now paused, awaiting human input. "
                f"The human can reply directly in this chat or use /workflows. "
                f"IMPORTANT: End this run now. The workflow will resume "
                f"immediately after the human responds."
            ),
        }
        if max_wait_seconds:
            result["max_wait_seconds"] = max_wait_seconds
            result["expires_at"] = action.get("expires_at")
            result["message"] += (
                f" If no response within {max_wait_seconds}s, "
                f"the workflow will auto-continue."
            )

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Failed to pause workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 10. workflow_submit_response
# ═══════════════════════════════════════════════════════════════════════════

def _handle_submit_response(args: Dict[str, Any], **kwargs: Any) -> str:
    action_id = (args.get("action_id") or "").strip()
    response = (args.get("response") or "").strip()

    if not action_id:
        return json.dumps({"error": "action_id is required"})
    if not response:
        return json.dumps({"error": "response is required"})

    try:
        db = get_db()
        resolved = db.resolve_pending_action(action_id, response)

        if resolved is None:
            return json.dumps({
                "success": False,
                "message": f"No pending action found with ID '{action_id}'. It may have already been resolved or dismissed.",
            })

        return json.dumps({
            "success": True,
            "action_id": action_id,
            "workflow_id": resolved["workflow_id"],
            "status": "resolved",
            "message": (
                f"Response submitted for '{action_id}'. "
                f"The workflow will see this response on its next scheduled run."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to submit response: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 11. workflow_list_pending
# ═══════════════════════════════════════════════════════════════════════════

def _handle_list_pending(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip() or None

    try:
        db = get_db()
        actions = db.list_pending_actions(workflow_id=workflow_id, status="pending")

        if not actions:
            return json.dumps({
                "pending_count": 0,
                "pending_actions": [],
                "message": "No pending workflows awaiting human input.",
            })

        formatted = []
        for a in actions:
            formatted.append({
                "action_id": a["id"],
                "workflow_id": a["workflow_id"],
                "question": a["question"],
                "context": a.get("context"),
                "created_at": a["created_at"],
            })

        return json.dumps({
            "pending_count": len(formatted),
            "pending_actions": formatted,
            "message": (
                f"Found {len(formatted)} pending action(s). "
                f"To respond, use workflow_submit_response(action_id, response)."
            ),
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to list pending actions: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# Handler map — used by __init__.py to wire schemas to implementations
# ═══════════════════════════════════════════════════════════════════════════

HANDLER_MAP = {
    "workflow_create": _handle_create,
    "workflow_update": _handle_update,
    "workflow_delete": _handle_delete,
    "workflow_list": _handle_list,
    "workflow_get": _handle_get,
    "workflow_save_state": _handle_save_state,
    "workflow_load_state": _handle_load_state,
    "workflow_delete_state": _handle_delete_state,
    "workflow_wait_for_user": _handle_wait_for_user,
    "workflow_submit_response": _handle_submit_response,
    "workflow_list_pending": _handle_list_pending,
}
