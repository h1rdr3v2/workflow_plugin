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
            "enabled": bool(wf["enabled"]),
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

    # Validate
    errors = []
    if not workflow_id:
        errors.append("id is required")
    if not name:
        errors.append("name is required")
    if not cron_expression:
        errors.append("cron_expression is required")
    if not prompt:
        errors.append("prompt is required")

    if len(cron_expression.split()) != 5:
        errors.append("cron_expression must have exactly 5 fields")

    if errors:
        return {"error": "; ".join(errors)}

    try:
        db = get_db()
        wf = db.create_workflow(workflow_id, name, cron_expression, prompt, description)
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
            update_kwargs[field] = str(body[field]).strip()

    if "enabled" in body and body["enabled"] is not None:
        update_kwargs["enabled"] = bool(body["enabled"])

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

    # Trigger the workflow immediately — don't wait for next cron tick
    try:
        import sys
        _plugin_root = Path(__file__).resolve().parent.parent
        if str(_plugin_root) not in sys.path:
            sys.path.insert(0, str(_plugin_root))
        from scheduler import trigger_workflow_now
        trigger_workflow_now(result["workflow_id"])
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
