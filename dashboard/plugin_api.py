#!/usr/bin/env python3
"""
Backend API routes for the Workflow Engine dashboard plugin.

Mounted by Hermes at /api/plugins/workflow/.
Exposes endpoints for the dashboard UI to manage pending workflows
and inspect workflow state.

The module must export a ``router`` (FastAPI APIRouter) — Hermes
calls ``app.include_router(router, prefix="/api/plugins/<name>/")``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the parent plugin directory is importable so we can reach db.py.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from db import get_db  # noqa: E402

# FastAPI is available in the dashboard process.
from fastapi import APIRouter, Request

router = APIRouter()


# ── Pending workflows ────────────────────────────────────────────────────


@router.get("/pending")
def list_pending() -> dict:
    """List all pending and resolved workflows."""
    db = get_db()
    pending = db.list_pending_actions(status="pending")
    resolved = db.list_pending_actions(status="resolved")

    return {
        "pending": [
            {
                "workflow_id": a["workflow_id"],
                "cron_job_id": a.get("cron_job_id"),
                "question": a["question"],
                "context": a.get("context"),
                "status": a["status"],
                "created_at": a["created_at"],
            }
            for a in pending
        ],
        "resolved": [
            {
                "workflow_id": a["workflow_id"],
                "cron_job_id": a.get("cron_job_id"),
                "question": a["question"],
                "context": a.get("context"),
                "status": a["status"],
                "response": a.get("response"),
                "created_at": a["created_at"],
                "responded_at": a.get("responded_at"),
            }
            for a in resolved
        ],
    }


@router.post("/respond")
async def respond(request: Request) -> dict:
    """Submit a human response to a pending workflow."""
    body = await request.json()
    workflow_id = (body.get("workflow_id") or "").strip()
    response = (body.get("response") or "").strip()

    if not workflow_id:
        return {"error": "workflow_id is required"}
    if not response:
        return {"error": "response is required"}

    db = get_db()
    result = db.resolve_pending_action(workflow_id, response)
    if result is None:
        return {"error": f"No pending workflow found with ID '{workflow_id}'"}

    return {"ok": True, "workflow_id": workflow_id, "status": "resolved"}


@router.post("/dismiss")
async def dismiss(request: Request) -> dict:
    """Dismiss a pending workflow without responding."""
    body = await request.json()
    workflow_id = (body.get("workflow_id") or "").strip()

    if not workflow_id:
        return {"error": "workflow_id is required"}

    db = get_db()
    deleted = db.delete_pending_action(workflow_id)
    if not deleted:
        return {"error": f"No pending workflow found with ID '{workflow_id}'"}

    return {"ok": True, "workflow_id": workflow_id, "status": "dismissed"}


# ── Workflow state inspection ─────────────────────────────────────────────


@router.get("/state")
def list_state() -> dict:
    """List all stored workflow state keys."""
    db = get_db()
    keys = db.list_state_keys()
    return {"keys": keys, "count": len(keys)}


@router.get("/state/{key:path}")
def get_state(key: str) -> dict:
    """Get the value for a specific state key."""
    if not key:
        return {"error": "key is required"}

    db = get_db()
    value = db.load_state(key)
    if value is None:
        return {"error": f"No state found for key '{key}'"}

    return {"key": key, "value": value}


@router.post("/state/delete")
async def delete_state(request: Request) -> dict:
    """Delete a workflow state key."""
    body = await request.json()
    key = (body.get("key") or "").strip()

    if not key:
        return {"error": "key is required"}

    db = get_db()
    deleted = db.delete_state(key)
    return {"ok": True, "key": key, "existed": deleted}


# ── Stats ─────────────────────────────────────────────────────────────────


@router.get("/stats")
def stats() -> dict:
    """Summary counts for the dashboard badge."""
    db = get_db()
    pending = db.list_pending_actions(status="pending")
    resolved = db.list_pending_actions(status="resolved")
    keys = db.list_state_keys()
    return {
        "pending_workflows": len(pending),
        "resolved_workflows": len(resolved),
        "state_keys": len(keys),
    }
