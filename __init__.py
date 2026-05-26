"""
Workflow Engine Plugin — Registration.

Provides persistent workflow state and human-in-the-loop interaction
for Hermes cron agents. Tools, hooks, slash commands, and a bundled
skill are all wired here.

Key components:
- 5 tools: save_state, load_state, wait_for_user, submit_response, list_pending
- pre_llm_call hook: injects previous state + pending responses into cron sessions
- /workflows slash command: human interface to review and respond to pending workflows
- workflow-agent skill: teaches the agent the workflow pattern
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import schemas, tools
from .db import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pre_llm_call hook — inject workflow context into cron sessions
# ---------------------------------------------------------------------------


def _inject_workflow_context(
    session_id: str,
    user_message: str,
    is_first_turn: bool,
    platform: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """
    Called before each LLM turn. When running inside a cron session,
    this hook injects:
    1. Previously saved workflow state relevant to the cron job
    2. Any pending human responses that the agent should act on

    Only injects on the first turn to avoid repeating context.
    Cron sessions are identified by session_id starting with 'cron_'.
    """
    # Only inject on the first turn
    if not is_first_turn:
        return None

    # Only inject for cron sessions
    if not session_id.startswith("cron_"):
        return None

    try:
        db = get_db()
    except Exception:
        return None  # DB unavailable — silently skip

    context_parts: List[str] = []
    context_parts.append("[WORKFLOW ENGINE — Persistent Context]")

    # ── 1. Identify the cron job ID from the session ──
    # Cron sessions use the format: cron_{job_id}_{timestamp}
    cron_job_id: Optional[str] = None
    parts = session_id.split("_", 1)
    if len(parts) > 1:
        # Extract job_id — it's everything between 'cron_' and the last timestamp segment
        remaining = parts[1]
        # The timestamp is the last 2 segments: YYYYMMDD_HHMMSS
        segments = remaining.rsplit("_", 2)
        if len(segments) >= 2:
            cron_job_id = segments[0] if len(segments) > 2 else remaining

    # ── 2. Inject pending workflow responses ──
    try:
        pending = db.list_pending_actions(status="pending")
        resolved_for_job = db.list_pending_actions(status="resolved", cron_job_id=cron_job_id)
    except Exception:
        pending = []
        resolved_for_job = []

    if resolved_for_job:
        context_parts.append("\n## Recent Human Responses")
        context_parts.append("The following workflows have been resolved by a human since your last run. Incorporate these responses into this execution:")
        for action in resolved_for_job[:10]:  # limit to avoid bloat
            response_preview = (action.get("response") or "")[:500]
            context_parts.append(
                f"- **{action['workflow_id']}**: {action.get('question', '')[:200]}\n"
                f"  Response: {response_preview}"
            )

    if pending:
        context_parts.append("\n## Pending Workflows (Awaiting Human Input)")
        context_parts.append("The following workflows are paused and waiting for a human response. If this run can contribute to any of them, check them:")
        for action in pending[:10]:
            context_parts.append(
                f"- **{action['workflow_id']}**: {action.get('question', '')[:200]}"
            )

    # ── 3. Inject previously saved state for this cron job ──
    if cron_job_id:
        try:
            keys = db.list_state_keys(prefix=f"cron:{cron_job_id}:")
            if not keys:
                keys = db.list_state_keys()  # fallback: all keys
        except Exception:
            keys = []

        if keys:
            context_parts.append("\n## Previously Saved Workflow State")
            context_parts.append("The following state was saved from prior runs. Use workflow_load_state() to retrieve full values as needed:")
            for key in keys[:20]:
                context_parts.append(f"- `{key}`")

    if len(context_parts) <= 1:
        return None  # nothing to inject

    context_text = "\n".join(context_parts)
    logger.debug("Injecting workflow context for session %s (%d parts)", session_id, len(context_parts))
    return {"context": context_text}


# ── Slash command: /workflows ─────────────────────────────────────────────


def _handle_workflows_slash(raw_args: str) -> str:
    """
    Handler for /workflows — lets humans review and respond to pending workflows.

    Usage:
      /workflows                    — list all pending workflows
      /workflows respond <id> <msg> — respond to a pending workflow
      /workflows dismiss <id>       — dismiss a pending workflow
    """
    args = raw_args.strip()

    if not args:
        # List all pending
        try:
            db = get_db()
            pending = db.list_pending_actions(status="pending")
            resolved = db.list_pending_actions(status="resolved")
        except Exception as e:
            return f"⚠️ Workflow engine unavailable: {e}"

        lines = ["📋 **Pending Workflows**\n"]
        if not pending:
            lines.append("No pending workflows awaiting input.")
        else:
            for i, action in enumerate(pending, 1):
                lines.append(
                    f"**{i}. `{action['workflow_id']}`**\n"
                    f"> {action.get('question', '')}\n"
                    f"  Created: {_fmt_time(action.get('created_at'))}\n"
                    f"  To respond: `/workflows respond {action['workflow_id']} <your answer>`"
                )
        if resolved:
            lines.append(f"\n_{len(resolved)} resolved workflow(s) — use `/workflows respond <id> ...` to add more._")

        lines.append("\n---\nCommands: `list` | `respond <id> <msg>` | `dismiss <id>`")
        return "\n".join(lines)

    # Parse subcommand
    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower()

    if subcommand == "respond" and len(parts) > 1:
        rest = parts[1].split(maxsplit=1)
        if len(rest) < 2:
            return "Usage: `/workflows respond <workflow_id> <your response>`"
        workflow_id = rest[0].strip()
        response = rest[1].strip()

        try:
            db = get_db()
            result = db.resolve_pending_action(workflow_id, response)
        except Exception as e:
            return f"⚠️ Error: {e}"

        if result is None:
            return f"⚠️ No pending workflow found with ID `{workflow_id}`."
        return f"✅ Response submitted for workflow `{workflow_id}`. The agent will see it on the next cron run."

    elif subcommand == "dismiss" and len(parts) > 1:
        workflow_id = parts[1].strip()
        try:
            db = get_db()
            deleted = db.delete_pending_action(workflow_id)
        except Exception as e:
            return f"⚠️ Error: {e}"

        if deleted:
            return f"🗑️ Dismissed workflow `{workflow_id}`."
        return f"⚠️ No pending workflow found with ID `{workflow_id}`."

    else:
        return f"Unknown subcommand: `{subcommand}`. Use `list`, `respond <id> <msg>`, or `dismiss <id>`."


def _fmt_time(ts: Optional[float]) -> str:
    """Format a Unix timestamp for display."""
    if ts is None:
        return "unknown"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


# ── register() — plugin entry point ───────────────────────────────────────


def register(ctx: Any) -> None:
    """
    Wire schemas to handlers, register hooks, slash commands, and skills.

    Called once at Hermes startup.
    """
    toolset = "workflow_engine"

    # ── Register all 5 tools ──────────────────────────────────────────
    ctx.register_tool(
        name="workflow_save_state",
        toolset=toolset,
        schema=schemas.WORKFLOW_SAVE_STATE,
        handler=tools._handle_save_state,
        description="Save persistent state across cron executions",
    )
    ctx.register_tool(
        name="workflow_load_state",
        toolset=toolset,
        schema=schemas.WORKFLOW_LOAD_STATE,
        handler=tools._handle_load_state,
        description="Load previously saved workflow state",
    )
    ctx.register_tool(
        name="workflow_wait_for_user",
        toolset=toolset,
        schema=schemas.WORKFLOW_WAIT_FOR_USER,
        handler=tools._handle_wait_for_user,
        description="Pause workflow and wait for human input",
    )
    ctx.register_tool(
        name="workflow_submit_response",
        toolset=toolset,
        schema=schemas.WORKFLOW_SUBMIT_RESPONSE,
        handler=tools._handle_submit_response,
        description="Submit a human response to resume a paused workflow",
    )
    ctx.register_tool(
        name="workflow_list_pending",
        toolset=toolset,
        schema=schemas.WORKFLOW_LIST_PENDING,
        handler=tools._handle_list_pending,
        description="List workflows awaiting human input",
    )

    # ── Register pre_llm_call hook ────────────────────────────────────
    ctx.register_hook("pre_llm_call", _inject_workflow_context)

    # ── Register slash command ────────────────────────────────────────
    try:
        ctx.register_command(
            "workflows",
            handler=_handle_workflows_slash,
            description="Review and respond to pending workflow approvals",
        )
    except Exception:
        logger.warning("Failed to register /workflows slash command", exc_info=True)

    # ── Register bundled skill ────────────────────────────────────────
    try:
        skills_dir = Path(__file__).parent / "skills"
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)
                logger.info("Registered skill: %s", child.name)
    except Exception:
        logger.warning("Failed to register workflow-agent skill", exc_info=True)

    logger.info(
        "Workflow Engine plugin registered (%d tools, 1 hook, 1 command, 1 skill)",
        5,
    )
