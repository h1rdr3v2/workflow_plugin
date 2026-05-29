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
from typing import Any, Dict, Optional

from .db import get_db
from .scheduler import schedule_workflow, unschedule_workflow

logger = logging.getLogger(__name__)

# ── Valid delivery targets ───────────────────────────────────────────────
VALID_DELIVER_VALUES = frozenset({"local", "discord", "telegram", "slack", "email", "origin"})


def _validate_deliver(deliver_value: str) -> str | None:
    """Validate a deliver value. Returns error message or None if valid."""
    v = (deliver_value or "").strip()
    if not v:
        return "deliver is required. Must be one of: " + ", ".join(sorted(VALID_DELIVER_VALUES))
    if v not in VALID_DELIVER_VALUES:
        return "Invalid deliver target '" + v + "'. Must be one of: " + ", ".join(sorted(VALID_DELIVER_VALUES))
    return None


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
    trigger_at = (args.get("trigger_at") or "").strip() or None
    trigger_in = (args.get("trigger_in") or "").strip() or None

    # Extract origin from args (always present — db.create_workflow defaults to {"platform": "web"})
    origin_raw = args.get("origin")
    origin: Optional[Dict[str, Any]] = None
    if isinstance(origin_raw, dict):
        origin = {
            "platform": (origin_raw.get("platform") or "").strip() or None,
            "chat_id": (origin_raw.get("chat_id") or "").strip() or None,
            "thread_id": (origin_raw.get("thread_id") or "").strip() or None,
            "user_id": (origin_raw.get("user_id") or "").strip() or None,
        }
        # Remove None values
        origin = {k: v for k, v in origin.items() if v is not None}
        if not origin:
            origin = None

    # Extract delivery target — required, no default
    deliver = (args.get("deliver") or "").strip()

    # Validation
    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})
    if not name:
        return json.dumps({"error": "name is required"})
    if not prompt:
        return json.dumps({"error": "prompt is required"})

    # Validate deliver
    deliver_error = _validate_deliver(deliver)
    if deliver_error:
        return json.dumps({"error": deliver_error})

    # Determine trigger type: cron, one-shot by timestamp, or one-shot by duration
    trigger_type = "cron"

    if trigger_at or trigger_in:
        trigger_type = "oneshot"
        # Compute a run-at time for the one-shot
        effective_cron = _compute_oneshot_cron(trigger_at, trigger_in)
        if effective_cron is None:
            return json.dumps({
                "error": (
                    "trigger_at must be a valid ISO 8601 timestamp "
                    "(e.g. '2026-05-29T14:30:00Z'), or trigger_in must be "
                    "a duration string (e.g. '5m', '1h', '30s')."
                )
            })
    else:
        # Cron mode — require and validate the expression
        if not cron_expression:
            return json.dumps({"error": "cron_expression is required when trigger_at and trigger_in are not provided"})

        effective_cron = cron_expression
        parts = effective_cron.split()
        if len(parts) != 5:
            return json.dumps({
                "error": f"cron_expression must have exactly 5 fields, got {len(parts)}. Example: '0 9 * * 1-5'"
            })

        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(effective_cron)
        except (ValueError, KeyError) as e:
            return json.dumps({
                "error": f"Invalid cron expression '{effective_cron}': {e}"
            })
        except ImportError:
            pass  # APScheduler not available — skip deep validation

    try:
        db = get_db()
        wf = db.create_workflow(
            workflow_id,
            name,
            effective_cron,
            prompt,
            description,
            origin=origin,
            trigger_type=trigger_type,
            deliver=deliver,
        )

        # Schedule it immediately (handles both cron and one-shot DateTrigger)
        schedule_workflow(wf)

        trigger_desc = effective_cron
        if trigger_type == "oneshot":
            if trigger_in:
                trigger_desc = f"in {trigger_in}"
            elif trigger_at:
                trigger_desc = f"at {trigger_at}"

        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "name": name,
            "cron_expression": effective_cron,
            "trigger_type": trigger_type,
            "message": (
                f"Workflow '{name}' created. "
                f"It will run {trigger_desc}."
            ),
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.exception("Failed to create workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to create workflow: {e}"})


def _compute_oneshot_cron(
    trigger_at: Optional[str],
    trigger_in: Optional[str],
) -> Optional[str]:
    """
    Compute an equivalent cron expression for a one-shot trigger.

    Returns a 5-field cron string representing the exact minute the
    workflow should fire, or None if the input is invalid.
    """
    import re
    from datetime import datetime, timezone

    run_time: Optional[datetime] = None

    if trigger_at:
        # Try parsing ISO 8601
        ts = trigger_at.replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                run_time = datetime.strptime(ts, fmt)
                if run_time.tzinfo is None:
                    run_time = run_time.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    elif trigger_in:
        # Parse duration string like "5m", "1h", "30s", "2d"
        match = re.match(r"^(\d+)\s*(s|m|h|d)$", trigger_in.strip())
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            now = datetime.now(timezone.utc)
            if unit == "s":
                run_time = now.replace(second=now.second + value)
                # Handle second overflow
                from datetime import timedelta
                run_time = now + timedelta(seconds=value)
            elif unit == "m":
                from datetime import timedelta
                run_time = now + timedelta(minutes=value)
            elif unit == "h":
                from datetime import timedelta
                run_time = now + timedelta(hours=value)
            elif unit == "d":
                from datetime import timedelta
                run_time = now + timedelta(days=value)

    if run_time is None:
        return None

    # Return a cron that matches exactly this minute
    return f"{run_time.minute} {run_time.hour} {run_time.day} {run_time.month} *"


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

        # Handle origin update
        if "origin" in args and args["origin"] is not None:
            origin_raw = args["origin"]
            if isinstance(origin_raw, dict):
                origin_clean = {
                    "platform": (origin_raw.get("platform") or "").strip() or None,
                    "chat_id": (origin_raw.get("chat_id") or "").strip() or None,
                    "thread_id": (origin_raw.get("thread_id") or "").strip() or None,
                    "user_id": (origin_raw.get("user_id") or "").strip() or None,
                }
                origin_clean = {k: v for k, v in origin_clean.items() if v is not None}
                update_kwargs["origin"] = origin_clean if origin_clean else None

        # Handle deliver update — validate if provided
        if "deliver" in args and args["deliver"] is not None:
            deliver_val = str(args["deliver"]).strip()
            err = _validate_deliver(deliver_val)
            if err:
                return json.dumps({"error": err})
            update_kwargs["deliver"] = deliver_val

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
        # Extract origin from kwargs (set by executor/__init__).
        # Fall back to the executor's active-origin registry if kwargs are empty.
        origin_platform = (kwargs.get("origin_platform") or "").strip() or None
        origin_chat_id = (kwargs.get("origin_chat_id") or "").strip() or None
        origin_thread_id = (kwargs.get("origin_thread_id") or "").strip() or None
        origin_user_id = (kwargs.get("origin_user_id") or "").strip() or None

        # Fallback: read from executor._active_origins if kwargs are empty
        if not origin_platform and not origin_chat_id:
            try:
                from .executor import get_active_origin
                active = get_active_origin(current_wf)
                if active:
                    origin_platform = active.get("origin_platform") or None
                    origin_chat_id = active.get("origin_chat_id") or None
                    origin_thread_id = active.get("origin_thread_id") or None
                    origin_user_id = active.get("origin_user_id") or None
            except ImportError:
                pass

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
# 12. workflow_send_message
# ═══════════════════════════════════════════════════════════════════════════

def _handle_send_message(args: Dict[str, Any], **kwargs: Any) -> str:
    message = (args.get("message") or "").strip()

    if not message:
        return json.dumps({"error": "message is required"})

    current_wf = _get_current_workflow_id(kwargs)
    if not current_wf:
        return json.dumps({
            "error": "No workflow context available. workflow_send_message can only be called during a workflow run."
        })

    try:
        db = get_db()
        wf = db.get_workflow(current_wf)
        if wf is None:
            return json.dumps({"error": f"Workflow '{current_wf}' not found."})

        # Every workflow has an origin
        origin = wf.get("origin") or {}
        deliver = (wf.get("deliver") or "").strip()

        if not deliver:
            return json.dumps({
                "success": False,
                "message": "Workflow has no deliver target set. Use workflow_update to set one.",
            })

        # Resolve delivery target: which platform + chat_id to use
        target_platform: Optional[str] = None
        target_chat_id: Optional[str] = None

        if deliver == "local":
            return json.dumps({
                "success": True,
                "message": "Message recorded (deliver=local — no gateway delivery).",
            })

        if deliver == "origin":
            # Send to the platform/chat where this workflow was created
            target_platform = (origin.get("platform") or "").strip() or None
            target_chat_id = (origin.get("chat_id") or "").strip() or None
        else:
            # deliver is a specific platform name like "discord", "telegram"
            target_platform = deliver
            target_chat_id = (origin.get("chat_id") or "").strip() or None

        if not target_platform:
            return json.dumps({
                "success": False,
                "message": f"Cannot deliver message: no target platform resolved (deliver={deliver}).",
            })

        if not target_chat_id:
            return json.dumps({
                "success": False,
                "message": (
                    f"Cannot deliver message to '{target_platform}': "
                    f"no chat_id in origin. Set origin.chat_id via "
                    f"workflow_update or the dashboard."
                ),
            })

        # Use the gateway reference to send the message
        try:
            from .scheduler import _gateway_ref
            gateway = _gateway_ref
        except ImportError:
            return json.dumps({"error": "Gateway reference not available — message delivery is only supported at runtime."})

        if gateway is None:
            return json.dumps({
                "success": False,
                "message": "Gateway reference not yet available. Try again later or use workflow_wait_for_user to interact.",
            })

        # Find matching adapter
        adapters = getattr(gateway, "adapters", {}) or {}
        sent = False
        for plat, adapter in adapters.items():
            plat_str = plat.value if hasattr(plat, "value") else str(plat)
            if plat_str.lower() == target_platform.lower():
                import asyncio
                loop = getattr(gateway, "loop", None)
                if loop and loop.is_running():
                    async def _send():
                        try:
                            await adapter.send(target_chat_id, message)
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(_send(), loop)
                    sent = True
                break

        if sent:
            return json.dumps({
                "success": True,
                "message": f"Message delivered to {target_platform} chat {target_chat_id}.",
            })
        else:
            return json.dumps({
                "success": False,
                "message": f"No adapter found for platform '{target_platform}'.",
            })

    except Exception as e:
        logger.exception("Failed to send message from workflow '%s'", current_wf)
        return json.dumps({"error": f"Failed to send message: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 13. workflow_disable
# ═══════════════════════════════════════════════════════════════════════════

def _handle_disable(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    try:
        db = get_db()
        result = db.update_workflow(workflow_id, enabled=False)

        if result is None:
            return json.dumps({
                "success": False,
                "message": f"Workflow '{workflow_id}' not found.",
            })

        # Remove from scheduler so it stops firing
        schedule_workflow(result)

        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "enabled": False,
            "message": f"Workflow '{workflow_id}' disabled/paused. It will not run again until re-enabled.",
        })
    except Exception as e:
        logger.exception("Failed to disable workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to disable workflow: {e}"})


# ═══════════════════════════════════════════════════════════════════════════
# 14. workflow_enable
# ═══════════════════════════════════════════════════════════════════════════

def _handle_enable(args: Dict[str, Any], **kwargs: Any) -> str:
    workflow_id = (args.get("workflow_id") or "").strip()

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    try:
        db = get_db()
        result = db.update_workflow(workflow_id, enabled=True)

        if result is None:
            return json.dumps({
                "success": False,
                "message": f"Workflow '{workflow_id}' not found.",
            })

        # Re-add to scheduler so it resumes firing
        schedule_workflow(result)

        return json.dumps({
            "success": True,
            "workflow_id": workflow_id,
            "enabled": True,
            "message": f"Workflow '{workflow_id}' re-enabled. It will resume running on its schedule.",
        })
    except Exception as e:
        logger.exception("Failed to enable workflow '%s'", workflow_id)
        return json.dumps({"error": f"Failed to enable workflow: {e}"})


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
    "workflow_send_message": _handle_send_message,
    "workflow_disable": _handle_disable,
    "workflow_enable": _handle_enable,
}
