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

        # Trigger the workflow immediately — don't wait for next cron tick.
        # Output delivery is handled by scheduler._deliver_workflow_output.
        try:
            from .scheduler import trigger_workflow_now
            trigger_workflow_now(result["workflow_id"])
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
        try:
            from .scheduler import set_gateway_ref
            set_gateway_ref(gateway)
        except Exception:
            pass

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

        # Trigger the workflow immediately — don't wait for next cron tick.
        # Output delivery is handled by scheduler._deliver_workflow_output.
        from .scheduler import trigger_workflow_now
        trigger_workflow_now(action["workflow_id"])

        # Send a confirmation back to the user via the gateway adapter
        _send_confirmation(gateway, platform_str, str(chat_id), action["id"])

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
    """Internal: invoke the agent in-process via AIAgent.

    Follows the same pattern as Hermes cron (cron/scheduler.py):
    resolve the runtime provider from config, construct AIAgent with
    the workflow tools, call run_conversation().  Everything runs
    in-process — no subprocess, no stdout markers, no env var dance.
    workflow_send_message has full gateway access.
    """
    import os
    import yaml
    from pathlib import Path
    from hermes_cli.runtime_provider import resolve_runtime_provider

    workflow_id = kw.get("workflow_id", "")
    run_id = kw.get("run_id", "")
    system_prompt = kw.get("system_prompt", "")
    user_message = kw.get("user_message", "")
    origin_platform = kw.get("origin_platform", "")
    origin_chat_id = kw.get("origin_chat_id", "")
    origin_thread_id = kw.get("origin_thread_id", "")
    origin_user_id = kw.get("origin_user_id", "")

    # ── Resolve model (same logic as cron/scheduler.py) ──────────────
    model = os.getenv("HERMES_MODEL", "")
    hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
    cfg_path = hermes_home / "config.yaml"
    _cfg: dict = {}
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                _cfg = yaml.safe_load(f) or {}
            _model_cfg = _cfg.get("model", {})
            if not model:
                if isinstance(_model_cfg, str):
                    model = _model_cfg
                elif isinstance(_model_cfg, dict):
                    model = _model_cfg.get("default", model)
        except Exception:
            pass

    # ── Resolve provider (api_key omitted — AIAgent resolves internally)
    try:
        runtime = resolve_runtime_provider()
    except Exception as e:
        raise RuntimeError(
            f"Failed to resolve provider for workflow '{workflow_id}': {e}"
        ) from e

    resolved_provider = runtime.get("provider", "")
    base_url = runtime.get("base_url")

    full_prompt = f"{system_prompt}\n\n{user_message}"

    from run_agent import AIAgent

    # Tell tool handlers which workflow this is
    tools.set_workflow_context(workflow_id, run_id)

    # Make configured MCP tools visible before AIAgent builds its tool list.
    # Cron does this for the same reason: registry discovery is not guaranteed
    # to have happened in this scheduler thread yet.
    try:
        from tools.mcp_tool import discover_mcp_tools
        discover_mcp_tools()
    except Exception:
        logger.debug("Workflow MCP tool discovery failed", exc_info=True)

    # Workflows get Hermes' normal tool surface: terminal, browser,
    # web/fetch, files, messaging, delegation, MCP tools, etc. Keep only
    # tools that are structurally wrong for autonomous workflow execution
    # disabled by default; operators can extend this via config.
    disabled = ["cronjob", "clarify"]
    # Layer on user-level disabled_toolsets from config.yaml
    try:
        agent_cfg = _cfg.get("agent") or {}
        user_disabled = agent_cfg.get("disabled_toolsets") or []
        for name in user_disabled:
            name = str(name).strip()
            if name and name not in disabled:
                disabled.append(name)
        workflow_cfg = _cfg.get("workflow") or {}
        workflow_disabled = workflow_cfg.get("disabled_toolsets") or []
        for name in workflow_disabled:
            name = str(name).strip()
            if name and name not in disabled:
                disabled.append(name)
    except Exception:
        pass

    # Bind the workflow origin as the active session context. This lets
    # Hermes tools such as send_message and background process notifications
    # route back to the same chat/thread when the workflow has an origin.
    session_tokens = None
    try:
        from gateway.session_context import set_session_vars
        session_tokens = set_session_vars(
            platform=str(origin_platform or ""),
            chat_id=str(origin_chat_id or ""),
            thread_id=str(origin_thread_id or ""),
            user_id=str(origin_user_id or ""),
            session_key=f"workflow:{workflow_id}",
        )
    except Exception:
        session_tokens = None

    agent = AIAgent(
        model=model,
        api_key=runtime.get("api_key"),
        provider=resolved_provider,
        base_url=base_url,
        api_mode=runtime.get("api_mode"),
        acp_command=runtime.get("command"),
        acp_args=runtime.get("args"),
        enabled_toolsets=None,  # All default tools — mirrors cron behavior
        disabled_toolsets=disabled,
        quiet_mode=True,
        platform=origin_platform or "workflow",
        chat_id=str(origin_chat_id or "") or None,
        thread_id=str(origin_thread_id or "") or None,
        user_id=str(origin_user_id or "") or None,
        gateway_session_key=f"workflow:{workflow_id}",
        session_id=f"wf_{workflow_id}_{run_id}",
        skip_memory=True,
        skip_context_files=True,
    )

    try:
        result = agent.run_conversation(full_prompt)
        summary = ""
        if isinstance(result, dict):
            summary = result.get("final_response", "") or result.get("text", "")
        return {"summary": summary}
    finally:
        tools.clear_workflow_context()
        if session_tokens is not None:
            try:
                from gateway.session_context import clear_session_vars
                clear_session_vars(session_tokens)
            except Exception:
                pass


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
