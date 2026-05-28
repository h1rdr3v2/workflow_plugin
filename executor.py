"""
Workflow Engine — Executor.

Runs a single workflow tick when triggered by the scheduler:
1. Load the workflow definition
2. Collect state, resolved pending actions, and prior run context
3. Build an enriched agent prompt
4. Invoke the Hermes agent (via plugin context)
5. Record the run outcome
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Optional

from .db import get_db

logger = logging.getLogger(__name__)

# Reference to the Hermes agent invocation function — set at registration time.
# Signature: (workflow_id: str, system_prompt: str, user_message: str, tools: list) -> dict
_agent_invoke: Optional[Callable[..., Dict[str, Any]]] = None


def set_agent_invoke(fn: Callable[..., Dict[str, Any]]) -> None:
    """Register the agent invocation callback from the Hermes plugin context."""
    global _agent_invoke
    _agent_invoke = fn


# ── Execution ─────────────────────────────────────────────────────────────


def execute(workflow_id: str) -> Dict[str, Any]:
    """
    Execute one tick of a workflow.

    Returns a dict with keys: workflow_id, status, started_at, finished_at,
    error_message (if status=='error'), pending_action_id (if status=='paused').

    Status values:
      - 'success'  — agent completed without pausing
      - 'paused'   — agent called workflow_wait_for_user
      - 'error'    — agent invocation failed
      - 'skipped'  — workflow not found or disabled
    """
    db = get_db()

    # ── 1. Load workflow ──────────────────────────────────────────────
    wf = db.get_workflow(workflow_id)
    if wf is None:
        logger.warning("Workflow '%s' not found — skipping", workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "skipped",
            "started_at": time.time(),
            "finished_at": time.time(),
            "error_message": "Workflow not found",
        }

    if not wf.get("enabled"):
        logger.debug("Workflow '%s' is disabled — skipping", workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "skipped",
            "started_at": time.time(),
            "finished_at": time.time(),
            "error_message": "Workflow is disabled",
        }

    # ── 2. Check for active runs (overlap protection) ─────────────────
    active = db.get_active_run(workflow_id)
    if active is not None:
        logger.info(
            "Workflow '%s' has an active/paused run — skipping to prevent overlap",
            workflow_id,
        )
        return {
            "workflow_id": workflow_id,
            "status": "skipped",
            "started_at": time.time(),
            "finished_at": time.time(),
            "error_message": "Previous run still active (pending human response)",
        }

    started_at = time.time()
    run_id = f"{workflow_id}_{int(started_at)}"
    run_info: Dict[str, Any] = {
        "workflow_id": workflow_id,
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
    }

    try:
        # ── 3. Gather context ─────────────────────────────────────────
        context = _build_context(db, workflow_id, wf)

        # ── 4. Build messages ─────────────────────────────────────────
        system_prompt = _build_system_prompt(wf, context)
        user_message = _build_user_message(wf, context)

        # ── 5. Extract origin for chat-based reply capture ────────────
        origin_kwargs: Dict[str, Any] = {}
        origin = wf.get("origin")
        if isinstance(origin, dict):
            origin_kwargs["origin_platform"] = origin.get("platform") or ""
            origin_chat_id = origin.get("chat_id")
            origin_kwargs["origin_chat_id"] = str(origin_chat_id) if origin_chat_id else ""
            origin_kwargs["origin_thread_id"] = origin.get("thread_id") or ""
            origin_kwargs["origin_user_id"] = origin.get("user_id") or ""

        # ── 6. Invoke agent ───────────────────────────────────────────
        if _agent_invoke is None:
            raise RuntimeError(
                "Agent invoke not registered — call executor.set_agent_invoke() "
                "during plugin registration"
            )

        result = _agent_invoke(
            workflow_id=workflow_id,
            run_id=run_id,
            system_prompt=system_prompt,
            user_message=user_message,
            **origin_kwargs,
        )

        # ── 7. Determine outcome ──────────────────────────────────────
        # The agent invocation returns a dict; we check if it paused
        finished_at = time.time()

        # Check if the agent created a pending action during this run
        pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")
        paused_action = None
        for pa in pending:
            if pa.get("run_id") == run_id or pa.get("created_at", 0) >= started_at:
                paused_action = pa
                break

        if paused_action:
            run_info.update({
                "status": "paused",
                "finished_at": finished_at,
                "pending_action_id": paused_action["id"],
            })
            logger.info(
                "Workflow '%s' paused — awaiting human response to '%s'",
                workflow_id,
                paused_action["id"],
            )
        else:
            run_info.update({
                "status": "success",
                "finished_at": finished_at,
                "output_summary": result.get("summary", ""),
            })
            logger.info("Workflow '%s' completed successfully", workflow_id)

    except Exception as e:
        finished_at = time.time()
        run_info.update({
            "status": "error",
            "finished_at": finished_at,
            "error_message": str(e),
        })
        logger.exception("Workflow '%s' failed with error", workflow_id)

    return run_info


# ── Context builders ──────────────────────────────────────────────────────


def _build_context(db: Any, workflow_id: str, wf: Dict[str, Any]) -> Dict[str, Any]:
    """Gather all context for the agent: state, pending, resolved responses."""
    context: Dict[str, Any] = {
        "workflow_id": workflow_id,
        "workflow_name": wf["name"],
        "workflow_description": wf.get("description", ""),
    }

    # All persisted state
    try:
        state = db.load_all_state(workflow_id)
        context["state"] = state
    except Exception as e:
        logger.warning("Failed to load state for '%s': %s", workflow_id, e)
        context["state"] = {}

    # Pending actions (human hasn't responded yet)
    try:
        pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")
        context["pending_actions"] = [
            {"id": pa["id"], "question": pa["question"], "created_at": pa["created_at"]}
            for pa in pending
        ]
    except Exception as e:
        logger.warning("Failed to load pending actions: %s", e)
        context["pending_actions"] = []

    # Recently resolved actions (human responded, agent should act on it)
    try:
        # Get resolved actions from the last 24 hours (or since epoch if first run)
        resolved = db.get_resolved_since(workflow_id, 0)
        context["resolved_responses"] = [
            {
                "id": pa["id"],
                "question": pa["question"],
                "response": pa["response"],
                "responded_at": pa["responded_at"],
            }
            for pa in resolved[-10:]  # last 10 to avoid context bloat
        ]
    except Exception as e:
        logger.warning("Failed to load resolved responses: %s", e)
        context["resolved_responses"] = []

    return context


def _build_system_prompt(wf: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Build the system prompt for the agent, with injected workflow context."""
    parts: list = []

    # The user-defined prompt is the core
    parts.append(wf["prompt"])

    # Inject workflow context
    parts.append("\n\n## Workflow Context")
    parts.append(f"You are running as workflow **{context['workflow_name']}** (`{context['workflow_id']}`).")

    # Inject saved state
    state = context.get("state", {})
    if state:
        parts.append("\n### Previously Saved State")
        parts.append("The following state was persisted from previous runs. Use workflow_load_state() to retrieve individual values as needed:")
        for key in sorted(state.keys()):
            val_preview = _preview(state[key], 100)
            parts.append(f"- **`{key}`**: `{val_preview}`")
    else:
        parts.append("\n### State")
        parts.append("No state has been saved yet. This may be the first run. Use workflow_save_state() to persist data for future runs.")

    # Inject resolved responses
    resolved = context.get("resolved_responses", [])
    if resolved:
        parts.append("\n### Human Responses Since Last Run")
        parts.append("The following questions were answered by a human (or timed out). Incorporate these into this execution:")
        for r in resolved:
            if r.get("timeout_status") == "expired":
                parts.append(f"- Q: {r['question']}\n  ⏰ **TIMEOUT** — the human did not respond within the time limit. Proceed without their input.")
            else:
                parts.append(f"- Q: {r['question']}\n  A: {r['response']}")

    # Inject pending actions
    pending = context.get("pending_actions", [])
    if pending:
        parts.append("\n### Pending Questions (Not Yet Answered)")
        parts.append("These questions are waiting for a human response. Do NOT re-ask them:")
        for p in pending:
            parts.append(f"- {p['question']}")

    # Instructions
    parts.append("\n\n## Instructions")
    parts.append("1. Use **workflow_load_state(key)** to retrieve any saved state you need.")
    parts.append("2. Make decisions based on the context and saved state.")
    parts.append("3. Use **workflow_save_state(key, value)** to persist important data for the next run.")
    parts.append("4. If you need human input, use **workflow_wait_for_user(question, context)** and then END — do not continue after pausing.")
    parts.append("5. If you see resolved responses above, incorporate them now.")
    parts.append("6. Be concise and actionable.")

    return "\n".join(parts)


def _build_user_message(wf: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Build the user message that triggers the agent to act."""
    resolved = context.get("resolved_responses", [])
    state = context.get("state", {})

    msg = f"Run the workflow **{context['workflow_name']}**."

    if resolved:
        msg += f"\n\n{len(resolved)} human response(s) are available since your last run. Please check them."

    if state:
        msg += f"\n\n{len(state)} state key(s) are available from prior runs."

    return msg


def _preview(value: Any, max_len: int = 100) -> str:
    """Create a short preview string for a value."""
    s = json.dumps(value) if not isinstance(value, str) else value
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s
