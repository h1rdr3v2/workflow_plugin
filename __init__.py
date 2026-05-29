"""
Workflow Engine Plugin — Registration.

The entry point for Hermes. Wires up all 11 tools, hooks, slash commands,
the scheduler, and the bundled workflow-agent skill.

Key components:
- 11 tools: create, update, delete, list, get, save/load/delete state,
  wait_for_user, submit_response, list_pending
- pre_llm_call hook: injects workflow state + pending responses into agent context
- plugin_shutdown hook: gracefully stops the scheduler
- /workflows slash command: human interface to review and respond
- workflow-agent skill: teaches the agent the workflow pattern
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import executor, schemas, tools
from . import scheduler
from .db import get_db
from .scheduler import shutdown as scheduler_shutdown
from .scheduler import start as scheduler_start

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pre_llm_call hook — inject workflow context into agent sessions
# ---------------------------------------------------------------------------


def _inject_workflow_context(
    session_id: str,
    user_message: str,
    is_first_turn: bool,
    platform: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """
    Called before each LLM turn. When running inside a workflow execution
    session, injects:
    1. Previously saved workflow state
    2. Any pending or recently resolved human responses

    Only injects on the first turn to avoid repeating context.
    """
    if not is_first_turn:
        return None

    # Extract workflow_id from the session context if available
    workflow_id = kwargs.get("workflow_id") or ""
    if not workflow_id:
        return None

    try:
        db = get_db()
    except Exception:
        return None

    context_parts: List[str] = []
    context_parts.append("[WORKFLOW ENGINE — Persistent Context]")

    # ── 1. Workflow info ──────────────────────────────────────────────
    wf = db.get_workflow(workflow_id)
    if wf is None:
        return None

    context_parts.append(f"\n## Workflow: {wf['name']} (`{workflow_id}`)")
    if wf.get("description"):
        context_parts.append(f"_{wf['description']}_")

    # ── 2. Saved state ────────────────────────────────────────────────
    try:
        state_keys = db.list_state_keys(workflow_id)
    except Exception:
        state_keys = []

    if state_keys:
        context_parts.append("\n## Previously Saved State")
        context_parts.append("The following state keys are available. Use workflow_load_state() to retrieve values:")
        for key in state_keys[:30]:
            context_parts.append(f"- `{key}`")
        if len(state_keys) > 30:
            context_parts.append(f"- ... and {len(state_keys) - 30} more keys")
    else:
        context_parts.append("\n## No saved state yet")
        context_parts.append("This appears to be the first run. Use workflow_save_state() to persist data for future runs.")

    # ── 3. Resolved responses ─────────────────────────────────────────
    try:
        resolved = db.get_resolved_since(workflow_id, 0)
    except Exception:
        resolved = []

    if resolved:
        context_parts.append("\n## Recent Human Responses")
        context_parts.append("These questions were answered by a human since your last run. Incorporate them:")
        for action in resolved[:10]:
            response_preview = (action.get("response") or "")[:500]
            context_parts.append(
                f"- **Q**: {action.get('question', '')[:200]}\n"
                f"  **A**: {response_preview}"
            )

    # ── 4. Still-pending actions ──────────────────────────────────────
    try:
        pending = db.list_pending_actions(workflow_id=workflow_id, status="pending")
    except Exception:
        pending = []

    if pending:
        context_parts.append("\n## Still Pending (Awaiting Human Input)")
        context_parts.append("These questions are waiting for a human response. Do NOT re-ask them:")
        for action in pending[:10]:
            context_parts.append(f"- {action.get('question', '')[:200]}")

    if len(context_parts) <= 1:
        return None

    context_text = "\n".join(context_parts)
    logger.debug("Injecting workflow context for session %s", session_id)
    return {"context": context_text}


# ── Slash command: /workflows ─────────────────────────────────────────────


def _handle_workflows_slash(raw_args: str) -> str:
    """
    Handler for /workflows — human interface to review and respond.

    Usage:
      /workflows                        — list all workflows and pending actions
      /workflows respond <id> <answer>  — respond to a pending action
      /workflows dismiss <id>           — dismiss a pending action
    """
    args = raw_args.strip()

    if not args:
        # Show summary: workflows + pending actions
        try:
            db = get_db()
            workflows = db.list_workflows()
            pending = db.list_pending_actions(status="pending")
        except Exception as e:
            return f"⚠️ Workflow engine unavailable: {e}"

        lines = ["📋 **Workflow Engine**\n"]

        # Workflows
        if not workflows:
            lines.append("No workflows defined. Create one with `workflow_create` or via the dashboard.")
        else:
            lines.append("### Scheduled Workflows")
            for wf in workflows:
                status_icon = "🟢" if wf["enabled"] else "🔴"
                lines.append(
                    f"{status_icon} **{wf['name']}** (`{wf['id']}`)\n"
                    f"   Schedule: `{wf['cron_expression']}`\n"
                    f"   Enabled: {'yes' if wf['enabled'] else 'no'}"
                )

        # Pending actions
        if pending:
            lines.append(f"\n### ⏳ Pending ({len(pending)} awaiting input)")
            for i, action in enumerate(pending, 1):
                lines.append(
                    f"**{i}. `{action['id']}`** — {action['workflow_id']}\n"
                    f"> {action.get('question', '')}\n"
                    f"  Created: {_fmt_time(action.get('created_at'))}\n"
                    f"  Respond: `/workflows respond {action['id']} <your answer>`"
                )
        else:
            lines.append("\n✅ No pending actions awaiting input.")

        lines.append("\n---\nCommands: `respond <id> <answer>` | `dismiss <id>`")
        return "\n".join(lines)

    # Parse subcommand
    parts = args.split(maxsplit=1)
    subcommand = parts[0].lower()

    if subcommand == "respond" and len(parts) > 1:
        rest = parts[1].split(maxsplit=1)
        if len(rest) < 2:
            return "Usage: `/workflows respond <action_id> <your response>`"
        action_id = rest[0].strip()
        response = rest[1].strip()

        try:
            db = get_db()
            result = db.resolve_pending_action(action_id, response)
        except Exception as e:
            return f"⚠️ Error: {e}"

        if result is None:
            return f"⚠️ No pending action found with ID `{action_id}`."

        # Trigger the workflow immediately — don't wait for next cron tick
        try:
            from .scheduler import trigger_workflow_now
            run_info = trigger_workflow_now(result["workflow_id"])

            # If there's output to deliver, try to send it
            if isinstance(run_info, dict) and run_info.get("status") == "success":
                summary = run_info.get("output_summary", "")
                if summary:
                    # Pending action stores origin fields at top level
                    platform = (result.get("origin_platform") or "").strip()
                    chat_id = (result.get("origin_chat_id") or "").strip()
                    if platform and chat_id:
                        from .scheduler import _gateway_ref
                        if _gateway_ref:
                            _deliver_output(
                                _gateway_ref,
                                platform,
                                chat_id,
                                result["workflow_id"],
                                summary,
                            )
        except Exception:
            pass  # Best-effort; the response is already recorded

        return (
            f"✅ Response submitted for `{action_id}`. "
            f"The workflow will resume immediately."
        )

    elif subcommand == "dismiss" and len(parts) > 1:
        action_id = parts[1].strip()
        try:
            db = get_db()
            dismissed = db.dismiss_pending_action(action_id)
        except Exception as e:
            return f"⚠️ Error: {e}"

        if dismissed:
            return f"🗑️ Dismissed pending action `{action_id}`."
        return f"⚠️ No pending action found with ID `{action_id}`."

    else:
        return (
            f"Unknown subcommand: `{subcommand}`. "
            f"Use `respond <id> <answer>` or `dismiss <id>`."
        )


def _fmt_time(ts: Optional[float]) -> str:
    """Format a Unix timestamp for display."""
    if ts is None:
        return "unknown"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


# ── plugin_shutdown hook ──────────────────────────────────────────────────


def _on_shutdown(**kwargs: Any) -> None:
    """Gracefully stop the scheduler when the plugin is unloaded."""
    scheduler_shutdown()


# ── pre_gateway_dispatch hook — capture chat replies for workflows ────────


def _handle_pre_gateway_dispatch(
    event: Any,
    gateway: Any,
    session_store: Any,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """
    Intercept incoming chat messages to capture workflow responses.

    When a workflow has a pending action for the same platform+chat_id,
    the user's reply is captured as the human response and the session
    agent never sees it (return {"action": "skip"}).

    If no matching pending action exists, returns None (normal dispatch).
    """
    try:
        source = getattr(event, "source", None)
        if source is None:
            return None

        platform = getattr(source, "platform", None)
        if platform is None:
            return None
        platform_str = platform.value if hasattr(platform, "value") else str(platform)

        chat_id = getattr(source, "chat_id", None)
        if not chat_id:
            return None

        text = getattr(event, "text", "") or ""

        db = get_db()
        action = db.find_pending_by_origin(platform_str, str(chat_id))

        if action is None:
            return None  # No pending action for this chat — normal dispatch

        # Capture the user's message as the response
        resolved = db.resolve_pending_action(action["id"], text)

        if resolved is None:
            return None  # Action was already resolved/dismissed

        logger.info(
            "pre_gateway_dispatch: captured reply for action %s (workflow=%s, platform=%s, chat=%s)",
            action["id"],
            action["workflow_id"],
            platform_str,
            chat_id,
        )

        # Store gateway reference for future use (e.g. sending confirmations)
        from .scheduler import set_gateway_ref
        set_gateway_ref(gateway)

        # Trigger the workflow immediately — don't wait for next cron tick
        from .scheduler import trigger_workflow_now
        run_info = trigger_workflow_now(action["workflow_id"])

        # Send a confirmation back to the user via the gateway adapter
        _send_confirmation(gateway, platform_str, str(chat_id), action["id"])

        # If the workflow completed with output, deliver it
        if isinstance(run_info, dict) and run_info.get("status") == "success":
            summary = run_info.get("output_summary", "")
            if summary:
                _deliver_output(
                    gateway,
                    platform_str,
                    str(chat_id),
                    action["workflow_id"],
                    summary,
                )

        # Skip — the session agent should not process this message
        return {"action": "skip", "reason": "workflow-response-captured"}

    except Exception:
        logger.exception("pre_gateway_dispatch hook failed")
        return None


def _send_confirmation(
    gateway: Any,
    platform_str: str,
    chat_id: str,
    action_id: str,
) -> None:
    """Try to send a confirmation message that the workflow response was captured."""
    _send_via_gateway(
        gateway,
        platform_str,
        chat_id,
        f"✅ Got it! Your response has been recorded for the workflow.",
    )


def _deliver_output(
    gateway: Any,
    platform_str: str,
    chat_id: str,
    workflow_id: str,
    summary: str,
) -> None:
    """Deliver the agent's final output summary to the originating chat.

    Uses the workflow's deliver field to determine routing. Falls back to
    the provided platform_str / chat_id for backward compatibility with
    callers that don't pass deliver info.
    """
    if not summary or not gateway:
        return

    # Try to read the deliver field from the workflow for smarter routing
    target_platform = platform_str
    target_chat_id = chat_id
    try:
        db = get_db()
        wf = db.get_workflow(workflow_id)
        if wf:
            deliver = (wf.get("deliver") or "").strip()
            origin = wf.get("origin") or {}
            if deliver == "local" or not deliver:
                return  # No gateway delivery needed
            if deliver == "origin":
                # Use the platform+chat where this workflow was created
                target_platform = (origin.get("platform") or platform_str).strip()
                target_chat_id = (origin.get("chat_id") or chat_id).strip()
            elif deliver != platform_str:
                # deliver specifies a different platform than the caller's.
                # We don't have a chat_id for that platform, so skip.
                return
    except Exception:
        pass  # Fall back to provided platform/chat_id

    if not target_platform or not target_chat_id:
        return

    _send_via_gateway(
        gateway,
        target_platform,
        target_chat_id,
        f"📋 **Workflow `{workflow_id}` completed**\n\n{summary}",
    )


def _send_via_gateway(
    gateway: Any,
    platform_str: str,
    chat_id: str,
    message: str,
) -> None:
    """Send a message through the gateway adapter matching platform_str."""
    try:
        adapters = getattr(gateway, "adapters", {}) or {}
        for plat, adapter in adapters.items():
            plat_str = plat.value if hasattr(plat, "value") else str(plat)
            if plat_str.lower() == platform_str.lower():
                import asyncio
                loop = getattr(gateway, "loop", None)
                if loop and loop.is_running():
                    async def _send():
                        try:
                            await adapter.send(chat_id, message)
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(_send(), loop)
                break
    except Exception:
        pass  # Best-effort; don't block on failure


# ── Agent invocation callback ─────────────────────────────────────────────


def _agent_invoke_factory(ctx: Any):
    """Create a closure that captures ctx for agent invocation."""
    def invoke(**kw: Any) -> Dict[str, Any]:
        return _invoke_with_context(ctx, **kw)
    return invoke


def _invoke_with_context(ctx: Any, **kw: Any) -> Dict[str, Any]:
    """Internal: invoke the agent via subprocess (``hermes -z``).

    PluginContext does not expose create_session / send_message — we shell
    out to ``hermes --oneshot`` with the workflow tools.  After the
    subprocess completes, we parse stdout for ``__WORKFLOW_DELIVER__``
    markers emitted by the workflow agent and deliver them through the
    parent process's gateway (which has access to the platform adapters).
    """
    import re
    import subprocess

    workflow_id = kw.get("workflow_id", "")
    run_id = kw.get("run_id", "")
    system_prompt = kw.get("system_prompt", "")
    user_message = kw.get("user_message", "")

    full_prompt = f"{system_prompt}\n\n{user_message}"

    try:
        hermes_bin = shutil.which("hermes") or "hermes"
        env = os.environ.copy()
        env["HERMES_WORKFLOW_ID"] = workflow_id
        result = subprocess.run(
            [hermes_bin, "-z", full_prompt, "-t", "workflow_engine"],
            capture_output=True, text=True, timeout=300,
            env=env,
        )

        output = (result.stdout or "").strip()

        # ── Extract and deliver workflow messages ──────────────────
        for match in re.finditer(r"__WORKFLOW_DELIVER__:(.*?)$", output, re.MULTILINE):
            try:
                payload = json.loads(match.group(1))
                _deliver_subprocess_message(payload)
            except Exception:
                logger.exception("Failed to deliver workflow subprocess message")

        # Strip markers from the output so they don't clutter the summary
        output = re.sub(
            r"__WORKFLOW_DELIVER__:.*?$", "", output, flags=re.MULTILINE,
        ).strip()

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.error(
                "hermes subprocess failed for workflow '%s' (rc=%d): %s",
                workflow_id, result.returncode, stderr[:500],
            )
            raise RuntimeError(
                f"hermes exited with code {result.returncode}: {stderr[:300]}"
            )

        return {"summary": output}

    except Exception as e:
        logger.exception("Agent invocation failed for workflow '%s'", workflow_id)
        raise


def _deliver_subprocess_message(payload: dict) -> None:
    """Deliver a message emitted by a subprocess workflow agent.

    Uses the parent process's gateway reference (set by the
    ``pre_gateway_dispatch`` hook).  If the gateway isn't available
    yet (e.g. cron-fired workflow before any user interaction), logs
    a warning and skips.
    """
    platform = (payload.get("platform") or "").strip()
    chat_id = (payload.get("chat_id") or "").strip()
    message = (payload.get("message") or "").strip()

    if not platform or not chat_id or not message:
        return

    from .scheduler import _gateway_ref

    gateway = _gateway_ref
    if gateway is None:
        logger.warning(
            "Cannot deliver subprocess message to %s/%s: gateway ref not set "
            "(no pre_gateway_dispatch has fired yet)",
            platform, chat_id,
        )
        return

    _send_via_gateway(gateway, platform, chat_id, message)


# ── register() — plugin entry point ───────────────────────────────────────


def register(ctx: Any) -> None:
    """
    Wire all tools, hooks, commands, skills, and start the scheduler.

    Called once at Hermes startup.
    """
    toolset = "workflow_engine"

    # ── Set up agent invocation ───────────────────────────────────────
    executor.set_agent_invoke(_agent_invoke_factory(ctx))
    scheduler.set_executor(executor.execute)

    # ── Register all 14 tools ─────────────────────────────────────────
    schema_map = {
        "workflow_create": schemas.WORKFLOW_CREATE,
        "workflow_update": schemas.WORKFLOW_UPDATE,
        "workflow_delete": schemas.WORKFLOW_DELETE,
        "workflow_list": schemas.WORKFLOW_LIST,
        "workflow_get": schemas.WORKFLOW_GET,
        "workflow_save_state": schemas.WORKFLOW_SAVE_STATE,
        "workflow_load_state": schemas.WORKFLOW_LOAD_STATE,
        "workflow_delete_state": schemas.WORKFLOW_DELETE_STATE,
        "workflow_wait_for_user": schemas.WORKFLOW_WAIT_FOR_USER,
        "workflow_submit_response": schemas.WORKFLOW_SUBMIT_RESPONSE,
        "workflow_list_pending": schemas.WORKFLOW_LIST_PENDING,
        "workflow_send_message": schemas.WORKFLOW_SEND_MESSAGE,
        "workflow_disable": schemas.WORKFLOW_DISABLE,
        "workflow_enable": schemas.WORKFLOW_ENABLE,
    }

    for name, schema in schema_map.items():
        handler = tools.HANDLER_MAP.get(name)
        if handler is None:
            logger.error("No handler registered for tool '%s'", name)
            continue

        ctx.register_tool(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            description=schema.get("description", "").split("\n")[0],
        )

    # ── Register hooks ────────────────────────────────────────────────
    ctx.register_hook("pre_llm_call", _inject_workflow_context)
    ctx.register_hook("pre_gateway_dispatch", _handle_pre_gateway_dispatch)
    ctx.register_hook("plugin_shutdown", _on_shutdown)

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
        if skills_dir.exists():
            for child in sorted(skills_dir.iterdir()):
                skill_md = child / "SKILL.md"
                if child.is_dir() and skill_md.exists():
                    ctx.register_skill(child.name, skill_md)
                    logger.info("Registered skill: %s", child.name)
    except Exception:
        logger.warning("Failed to register workflow-agent skill", exc_info=True)

    # ── Start the scheduler ───────────────────────────────────────────
    try:
        db = get_db()
        workflows = db.list_workflows(enabled_only=True)
        scheduler_start(workflows)
    except Exception:
        logger.warning("Failed to start workflow scheduler", exc_info=True)

    logger.info(
        "Workflow Engine v1.0.0 registered (%d tools, 3 hooks, 1 command, 1 skill)",
        len(schema_map),
    )
