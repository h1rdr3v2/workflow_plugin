#!/usr/bin/env python3
"""
Backend API routes for the Workflow Engine dashboard plugin.

Mounted by Hermes at /api/plugins/workflow/.
Exposes endpoints for:
- Workflow CRUD (list, get, create, update, delete)
- Per-workflow state inspection
- Pending action management (list, respond, dismiss)
- Manual workflow run trigger
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the parent plugin directory is importable
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from db import get_db  # noqa: E402
from scheduler import schedule_workflow, unschedule_workflow, get_jobs_status  # noqa: E402

from fastapi import APIRouter, Request

router = APIRouter()

# ── Deliver validation (inlined from tools.py to avoid relative-import trap) ──

VALID_DELIVER_VALUES = frozenset({"local", "discord", "telegram", "slack", "email", "origin"})
VALID_NOTIFY_VALUES = frozenset({"summary", "minimal", "silent"})


def _validate_deliver(deliver_value: str) -> str | None:
    """Validate a deliver value. Returns error message or None if valid."""
    v = (deliver_value or "").strip()
    if not v:
        return "deliver is required. Must be one of: " + ", ".join(sorted(VALID_DELIVER_VALUES))
    if v not in VALID_DELIVER_VALUES:
        return "Invalid deliver target '" + v + "'. Must be one of: " + ", ".join(sorted(VALID_DELIVER_VALUES))
    return None


def _validate_notify(notify_value: str) -> str | None:
    """Validate a notify value. Returns error message or None if valid."""
    v = (notify_value or "").strip()
    if v not in VALID_NOTIFY_VALUES:
        return "Invalid notify mode '" + v + "'. Must be one of: " + ", ".join(sorted(VALID_NOTIFY_VALUES))
    return None


def _compute_oneshot_cron(trigger_at: str | None, trigger_in: str | None) -> str | None:
    """Compute an equivalent cron expression for a one-shot trigger."""
    import re
    from datetime import datetime, timedelta, timezone

    run_time = None

    if trigger_at:
        ts = trigger_at.replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z",
            "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        ):
            try:
                run_time = datetime.strptime(ts, fmt)
                if run_time.tzinfo is None:
                    run_time = run_time.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    elif trigger_in:
        match = re.match(r"^(\d+)\s*(s|m|h|d)$", trigger_in.strip())
        if match:
            value, unit = int(match.group(1)), match.group(2)
            now = datetime.now(timezone.utc)
            if unit == "s":
                run_time = now + timedelta(seconds=value)
            elif unit == "m":
                run_time = now + timedelta(minutes=value)
            elif unit == "h":
                run_time = now + timedelta(hours=value)
            elif unit == "d":
                run_time = now + timedelta(days=value)

    if run_time is None:
        return None
    return f"{run_time.minute} {run_time.hour} {run_time.day} {run_time.month} *"


# ═══════════════════════════════════════════════════════════════════════════
# Workflow CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/workflows")
def list_workflows() -> dict:
    """List all workflows with scheduler status."""
    db = get_db()
    workflows = db.list_workflows()

    # Enrich with scheduler job info
    jobs = {j["id"]: j for j in get_jobs_status()}

    enriched = []
    for wf in workflows:
        jid = f"workflow:{wf['id']}"
        job_info = jobs.get(jid, {})
        enriched.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf.get("description", ""),
            "cron_expression": wf["cron_expression"],
            "prompt": wf.get("prompt", ""),
            "enabled": bool(wf["enabled"]),
            "origin": wf.get("origin"),
            "deliver": wf.get("deliver", "local"),
            "notify": wf.get("notify", "summary"),
            "trigger_type": wf.get("trigger_type", "cron"),
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
            "next_run": job_info.get("next_run"),
            "scheduled": jid in jobs,
        })

    return {
        "workflows": enriched,
        "count": len(enriched),
    }


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict:
    """Get full details of a single workflow."""
    db = get_db()
    wf = db.get_workflow(workflow_id)

    if wf is None:
        return {"error": f"Workflow '{workflow_id}' not found", "found": False}

    state_keys = db.list_state_keys(workflow_id)
    state = db.load_all_state(workflow_id)
    pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")
    resolved = db.list_pending_actions(workflow_id=workflow_id, status="resolved")

    # Get scheduler info
    jobs = {j["id"]: j for j in get_jobs_status()}
    jid = f"workflow:{workflow_id}"
    job_info = jobs.get(jid, {})

    return {
        "found": True,
        "workflow": {
            "id": wf["id"],
            "name": wf["name"],
            "description": wf.get("description", ""),
            "cron_expression": wf["cron_expression"],
            "prompt": wf["prompt"],
            "enabled": bool(wf["enabled"]),
            "origin": wf.get("origin"),
            "deliver": wf.get("deliver", "local"),
            "notify": wf.get("notify", "summary"),
            "trigger_type": wf.get("trigger_type", "cron"),
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
            "next_run": job_info.get("next_run"),
            "scheduled": jid in jobs,
        },
        "state_keys": state_keys,
        "state": state,
        "state_count": len(state_keys),
        "pending_actions": [
            {
                "id": pa["id"],
                "question": pa["question"],
                "context": pa.get("context"),
                "expires_at": pa.get("expires_at"),
                "created_at": pa["created_at"],
            }
            for pa in pending
        ],
        "pending_count": len(pending),
        "resolved_actions": [
            {
                "id": pa["id"],
                "question": pa["question"],
                "response": pa.get("response"),
                "responded_at": pa.get("responded_at"),
            }
            for pa in resolved
        ],
        "resolved_count": len(resolved),
    }


@router.post("/workflows")
async def create_workflow(request: Request) -> dict:
    """Create a new workflow and schedule it."""
    body = await request.json()

    workflow_id = (body.get("id") or body.get("workflow_id") or "").strip()
    name = (body.get("name") or "").strip()
    cron_expression = (body.get("cron_expression") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    description = (body.get("description") or "").strip()
    origin = body.get("origin")  # optional dict
    deliver = (body.get("deliver") or "").strip()
    notify = (body.get("notify") or "summary").strip()
    trigger_at = (body.get("trigger_at") or "").strip() or None
    trigger_in = (body.get("trigger_in") or "").strip() or None

    # Validate
    errors = []
    if not workflow_id:
        errors.append("id is required")
    if not name:
        errors.append("name is required")
    if not prompt:
        errors.append("prompt is required")

    # Validate deliver (required for dashboard, no default)
    err = _validate_deliver(deliver)
    if err:
        errors.append(err)

    # Validate notify
    notify_err = _validate_notify(notify)
    if notify_err:
        errors.append(notify_err)

    # Determine trigger type
    trigger_type = "cron"
    if trigger_at or trigger_in:
        trigger_type = "oneshot"
        # Compute cron expression for the one-shot
        effective_cron = _compute_oneshot_cron(trigger_at, trigger_in)
        if effective_cron is None:
            errors.append("trigger_at must be a valid ISO 8601 timestamp, or trigger_in must be a duration like '5m', '1h', '30s'")
    else:
        effective_cron = cron_expression
        if not cron_expression:
            errors.append("cron_expression is required when trigger_at and trigger_in are not provided")
        elif len(cron_expression.split()) != 5:
            errors.append("cron_expression must have exactly 5 fields")

    # Real cron validation (only for cron-type workflows)
    if not errors and trigger_type == "cron":
        try:
            from croniter import croniter
            croniter(effective_cron)
        except (ValueError, KeyError) as e:
            errors.append(f"Invalid cron expression: {e}")

    if errors:
        return {"error": "; ".join(errors)}

    try:
        db = get_db()
        wf = db.create_workflow(
            workflow_id, name, effective_cron, prompt, description,
            origin=origin if isinstance(origin, dict) else None,
            trigger_type=trigger_type,
            deliver=deliver,
            notify=notify,
        )
        schedule_workflow(wf)

        return {
            "success": True,
            "workflow": wf,
            "message": f"Workflow '{name}' created and scheduled.",
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Failed to create workflow: {e}"}


@router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, request: Request) -> dict:
    """Update an existing workflow."""
    body = await request.json()

    update_kwargs = {}
    for field in ("name", "description", "cron_expression", "prompt"):
        if field in body and body[field] is not None:
            val = str(body[field]).strip()
            # Validate cron if being updated
            if field == "cron_expression" and val:
                if len(val.split()) != 5:
                    return {"error": "cron_expression must have exactly 5 fields"}
                try:
                    from croniter import croniter
                    croniter(val)
                except (ValueError, KeyError) as e:
                    return {"error": f"Invalid cron expression: {e}"}
            update_kwargs[field] = val

    if "enabled" in body and body["enabled"] is not None:
        update_kwargs["enabled"] = bool(body["enabled"])

    if "origin" in body and body["origin"] is not None:
        origin = body["origin"]
        if isinstance(origin, dict):
            update_kwargs["origin"] = origin

    if "deliver" in body and body["deliver"] is not None:
        deliver_val = str(body["deliver"]).strip()
        err = _validate_deliver(deliver_val)
        if err:
            return {"error": err}
        update_kwargs["deliver"] = deliver_val

    if "notify" in body and body["notify"] is not None:
        notify_val = str(body["notify"]).strip()
        err = _validate_notify(notify_val)
        if err:
            return {"error": err}
        update_kwargs["notify"] = notify_val

    if "trigger_type" in body and body["trigger_type"] is not None:
        update_kwargs["trigger_type"] = str(body["trigger_type"]).strip()

    try:
        db = get_db()
        result = db.update_workflow(workflow_id, **update_kwargs)

        if result is None:
            return {"error": f"Workflow '{workflow_id}' not found"}

        schedule_workflow(result)

        return {
            "success": True,
            "workflow": result,
            "message": f"Workflow '{workflow_id}' updated.",
        }
    except Exception as e:
        return {"error": f"Failed to update workflow: {e}"}


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str) -> dict:
    """Delete a workflow and all its state/pending actions."""
    try:
        db = get_db()
        existed = db.delete_workflow(workflow_id)

        if existed:
            unschedule_workflow(workflow_id)
            return {
                "success": True,
                "message": f"Workflow '{workflow_id}' deleted.",
            }
        else:
            return {"error": f"Workflow '{workflow_id}' not found"}
    except Exception as e:
        return {"error": f"Failed to delete workflow: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
# Workflow State
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/workflows/{workflow_id}/state")
def get_workflow_state(workflow_id: str) -> dict:
    """Get all state key-value pairs for a workflow."""
    db = get_db()

    if not db.workflow_exists(workflow_id):
        return {"error": f"Workflow '{workflow_id}' not found"}

    keys = db.list_state_keys(workflow_id)
    state = db.load_all_state(workflow_id)

    return {
        "workflow_id": workflow_id,
        "state_keys": keys,
        "state": state,
        "count": len(keys),
    }


@router.delete("/workflows/{workflow_id}/state/{key:path}")
def delete_workflow_state(workflow_id: str, key: str) -> dict:
    """Delete a specific state key for a workflow."""
    db = get_db()
    existed = db.delete_state(workflow_id, key)

    return {
        "success": True,
        "deleted": existed,
        "workflow_id": workflow_id,
        "key": key,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pending Actions
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/pending")
def list_pending(workflow_id: str | None = None) -> dict:
    """List pending human-input requests."""
    db = get_db()
    pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")
    resolved = db.list_pending_actions(workflow_id=workflow_id, status="resolved")

    return {
        "pending": [
            {
                "id": a["id"],
                "workflow_id": a["workflow_id"],
                "question": a["question"],
                "context": a.get("context"),
                "expires_at": a.get("expires_at"),
                "created_at": a["created_at"],
            }
            for a in pending
        ],
        "pending_count": len(pending),
        "resolved": [
            {
                "id": a["id"],
                "workflow_id": a["workflow_id"],
                "question": a["question"],
                "response": a.get("response"),
                "responded_at": a.get("responded_at"),
            }
            for a in resolved
        ],
        "resolved_count": len(resolved),
    }


@router.post("/pending/{action_id}/respond")
async def respond_to_pending(action_id: str, request: Request) -> dict:
    """Submit a human response to a pending action."""
    body = await request.json()
    response = (body.get("response") or "").strip()

    if not response:
        return {"error": "response is required"}

    db = get_db()
    result = db.resolve_pending_action(action_id, response)

    if result is None:
        return {"error": f"No pending action found with ID '{action_id}'"}

    # Resume immediately — don't wait for next cron tick. Run off-thread so this
    # request handler never blocks on a full agent run; delivery is handled by
    # the scheduler.
    try:
        import sys
        _plugin_root = Path(__file__).resolve().parent.parent
        if str(_plugin_root) not in sys.path:
            sys.path.insert(0, str(_plugin_root))
        from scheduler import trigger_workflow_now_async
        trigger_workflow_now_async(result["workflow_id"])
    except Exception:
        pass  # Best-effort; response is already recorded

    return {
        "success": True,
        "action_id": action_id,
        "workflow_id": result["workflow_id"],
        "message": "Response submitted. The workflow will resume immediately.",
    }


@router.post("/pending/{action_id}/dismiss")
def dismiss_pending(action_id: str) -> dict:
    """Dismiss a pending action without responding."""
    db = get_db()
    dismissed = db.dismiss_pending_action(action_id)

    if not dismissed:
        return {"error": f"No pending action found with ID '{action_id}'"}

    return {
        "success": True,
        "action_id": action_id,
        "message": "Pending action dismissed.",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Manual Run Trigger
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/workflows/{workflow_id}/run")
def trigger_workflow_run(workflow_id: str) -> dict:
    """
    Manually trigger a workflow run immediately (ad-hoc execution,
    does not affect the regular schedule).
    """
    from executor import execute  # noqa: E402

    db = get_db()
    wf = db.get_workflow(workflow_id)

    if wf is None:
        return {"error": f"Workflow '{workflow_id}' not found"}

    active = db.get_active_run(workflow_id)
    if active is not None:
        return {
            "success": False,
            "message": "Workflow has an active/paused run. Wait for it to resolve or respond to pending actions first.",
        }

    try:
        result = execute(workflow_id)
        return {
            "success": True,
            "run": result,
            "message": f"Workflow '{workflow_id}' executed. Status: {result.get('status')}",
        }
    except Exception as e:
        return {"error": f"Failed to execute workflow: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
# Scheduler Status
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/scheduler")
def scheduler_status() -> dict:
    """Get the status of the scheduler and all scheduled jobs."""
    return {
        "jobs": get_jobs_status(),
        "job_count": len(get_jobs_status()),
    }
