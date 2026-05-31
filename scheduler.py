"""
Workflow Engine — Scheduler.

Lightweight polling-based scheduler using ``croniter`` (Hermes core dep).
Follows the same pattern as ``gateway/run.py:_start_cron_ticker``.

Manages:
- Cron-based recurring workflows (polled)
- One-shot workflows (``threading.Timer``)
- Timeout checker (every 30s in the poll loop)
- Overlap protection (skips if a paused run exists)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── croniter ──────────────────────────────────────────────────────────────
# Core Hermes dependency — always available.  No lazy check needed.

from croniter import croniter  # type: ignore[import-untyped]


# ── Scheduler state ───────────────────────────────────────────────────────

_executor: Optional[Callable[..., Any]] = None
_gateway_ref: Any = None

_workflows: Dict[str, Dict[str, Any]] = {}       # workflow_id → workflow dict
_next_runs: Dict[str, float] = {}                  # workflow_id → next epoch
_timers: Dict[str, threading.Timer] = {}           # workflow_id → one-shot timer
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()

# Minimum sleep between poll iterations to avoid busy-waiting
_MIN_SLEEP = 0.5
_MAX_SLEEP = 60.0


def set_executor(fn: Callable[..., Any]) -> None:
    """Register the executor function the scheduler will call on each tick."""
    global _executor
    _executor = fn


# ── Gateway ref —──────────────────────────────────────────────────────────

def set_gateway_ref(gateway: Any) -> None:
    """Store a reference to the live GatewayRunner for message delivery.

    This is only an optimization: :func:`_resolve_gateway` also recovers the
    runner from Hermes' process-global weakref, so workflow messages are
    delivered even when no inbound message has primed this reference via the
    ``pre_gateway_dispatch`` hook (e.g. a one-shot firing from its timer, or a
    workflow created from the dashboard).
    """
    global _gateway_ref
    _gateway_ref = gateway


def _resolve_gateway() -> Any:
    """Return the live Hermes GatewayRunner, or None when unavailable.

    Prefers an explicitly-stored reference, then falls back to the
    process-global weakref Hermes sets in ``GatewayRunner.__init__``
    (``gateway.run._gateway_runner_ref``). This removes the dependence on an
    inbound message having primed the reference first.
    """
    global _gateway_ref
    if _gateway_ref is not None:
        return _gateway_ref
    try:
        from gateway.run import _gateway_runner_ref
        gateway = _gateway_runner_ref()
    except Exception:
        return None
    if gateway is not None:
        _gateway_ref = gateway
    return gateway


def _running_gateway_loop(gateway: Any) -> Any:
    """Return the gateway's running asyncio loop, or None.

    The GatewayRunner stores its loop on ``_gateway_loop``; tolerate a plain
    ``loop`` attribute too in case that ever changes.
    """
    loop = getattr(gateway, "_gateway_loop", None) or getattr(gateway, "loop", None)
    if loop is not None and getattr(loop, "is_running", lambda: False)():
        return loop
    return None


def _find_adapter(gateway: Any, platform_str: str) -> Any:
    """Return the connected adapter whose platform matches ``platform_str``."""
    adapters = getattr(gateway, "adapters", None) or {}
    for plat, adapter in adapters.items():
        plat_value = plat.value if hasattr(plat, "value") else str(plat)
        if plat_value.lower() == platform_str.lower():
            return adapter
    return None


# ── Schedule / unschedule ─────────────────────────────────────────────────

def _parse_cron_to_next(cron_expr: str, base_time: Optional[datetime] = None) -> float:
    """Return the next run time (epoch seconds) for a cron expression."""
    now = base_time or datetime.now(timezone.utc)
    it = croniter(cron_expr, now)
    return it.get_next(float)


def schedule_workflow(workflow: Dict[str, Any]) -> None:
    """
    Add or update a workflow in the poll schedule.

    For cron workflows: computes next-run time and adds to the poll table.
    For one-shot workflows: schedules a ``threading.Timer``.
    Disabled workflows are removed from the schedule.
    """
    wf_id: str = workflow["id"]

    with _lock:
        # Cancel any existing timer / remove from tables
        _cancel_timer(wf_id)
        _workflows.pop(wf_id, None)
        _next_runs.pop(wf_id, None)

        if not workflow.get("enabled", True):
            logger.debug("Workflow %s is disabled — not scheduling", wf_id)
            return

        trigger_type = workflow.get("trigger_type", "cron")
        cron_expr = workflow.get("cron_expression", "")

        if trigger_type == "oneshot":
            _schedule_oneshot(wf_id, cron_expr)
            return

        # Recurring cron workflow
        try:
            next_ts = _parse_cron_to_next(cron_expr)
        except (ValueError, KeyError) as e:
            logger.error("Invalid cron for '%s': %s — %s", wf_id, cron_expr, e)
            return

        _workflows[wf_id] = workflow
        _next_runs[wf_id] = next_ts

    logger.info(
        "Scheduled workflow '%s' (%s): type=%s cron='%s' next_run=%s",
        workflow.get("name", "?"),
        wf_id,
        trigger_type,
        cron_expr,
        datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat()
        if trigger_type == "cron" else "now",
    )


def _schedule_oneshot(wf_id: str, cron_expr: str) -> None:
    """Schedule a one-shot workflow via threading.Timer."""
    try:
        parts = cron_expr.split()
        if len(parts) != 5:
            logger.error("Invalid one-shot cron for %s: %s", wf_id, cron_expr)
            return

        now = datetime.now(timezone.utc)
        run_time = datetime(
            year=now.year,
            month=int(parts[3]),
            day=int(parts[2]),
            hour=int(parts[1]),
            minute=int(parts[0]),
            tzinfo=timezone.utc,
        )

        delay = (run_time - now).total_seconds()
        if delay <= 0:
            # Already past due — fire immediately (but in a thread to avoid blocking)
            logger.info("One-shot '%s' is past due — firing now", wf_id)
            threading.Thread(target=_fire, args=(wf_id,), daemon=True).start()
        else:
            timer = threading.Timer(delay, _fire, args=[wf_id])
            timer.daemon = True
            timer.start()
            _timers[wf_id] = timer
            logger.info(
                "One-shot '%s' scheduled in %.0fs (at %s)",
                wf_id,
                delay,
                run_time.isoformat(),
            )
    except Exception:
        logger.exception("Failed to schedule one-shot '%s'", wf_id)


def _cancel_timer(wf_id: str) -> None:
    """Cancel and remove a one-shot timer if present."""
    timer = _timers.pop(wf_id, None)
    if timer is not None:
        timer.cancel()


def unschedule_workflow(workflow_id: str) -> None:
    """Remove a workflow from the schedule."""
    with _lock:
        _cancel_timer(workflow_id)
        existed = _workflows.pop(workflow_id, None) is not None or _next_runs.pop(workflow_id, None) is not None
    if existed:
        logger.info("Unscheduled workflow: %s", workflow_id)


def schedule_all_workflows(workflows: list) -> None:
    """Schedule all enabled workflows. Called on startup."""
    for wf in workflows:
        schedule_workflow(wf)
    logger.info("Scheduled %d workflows", len(workflows))


# ── Polling loop —─────────────────────────────────────────────────────────

def _poll_loop() -> None:
    """Main polling loop.  Checks for due workflows, fires them, handles timeouts."""
    logger.info("Workflow poll loop started")

    while not _stop_event.is_set():
        loop_start = time.time()

        # ── Fire due workflows ────────────────────────────────────────
        with _lock:
            now = time.time()
            due_ids = [wid for wid, ts in _next_runs.items() if ts <= now]

        for wid in due_ids:
            _fire(wid)

        # ── Recompute next runs for fired workflows ───────────────────
        with _lock:
            for wid in due_ids:
                wf = _workflows.get(wid)
                if wf and wf.get("enabled") and wf.get("trigger_type", "cron") == "cron":
                    try:
                        _next_runs[wid] = _parse_cron_to_next(wf["cron_expression"])
                    except Exception:
                        logger.exception("Failed to recompute next run for '%s' — removing", wid)
                        _workflows.pop(wid, None)
                        _next_runs.pop(wid, None)

        # ── Timeout checker (every 30s) ───────────────────────────────
        _run_timeout_checker()

        # ── Compute sleep duration ────────────────────────────────────
        with _lock:
            run_times = list(_next_runs.values())
            if run_times:
                next_due = min(run_times)
                sleep_for = max(_MIN_SLEEP, min(next_due - time.time(), _MAX_SLEEP))
            else:
                sleep_for = _MAX_SLEEP

        elapsed = time.time() - loop_start
        actual_sleep = max(0, sleep_for - elapsed)
        _stop_event.wait(actual_sleep)

    logger.info("Workflow poll loop stopped")


def _fire(workflow_id: str) -> None:
    """Execute one tick of a workflow via the registered executor."""
    if _executor is None:
        logger.warning("No executor registered — skipping tick for '%s'", workflow_id)
        return

    logger.info("Workflow tick: %s", workflow_id)
    try:
        run_info = _executor(workflow_id)

        # ── Deliver output to the user ────────────────────────────────
        if isinstance(run_info, dict):
            _deliver_workflow_output(workflow_id, run_info)

            # Auto-disable one-shot workflows after they fire
            _auto_disable_oneshot(workflow_id, run_info)

    except Exception:
        logger.exception("Unhandled error in executor for workflow '%s'", workflow_id)


def _deliver_workflow_output(workflow_id: str, run_info: Dict[str, Any]) -> None:
    """
    Deliver workflow output to the configured delivery target.

    Mirrors cron's _deliver_result: resolves the delivery target from the
    workflow's deliver field + origin, then sends via the gateway adapter.

    The per-workflow ``notify`` setting controls success delivery:
      - ``summary`` (default): send the agent's final summary
      - ``minimal``: send a static "✅ Workflow <name> completed" line
      - ``silent``: send nothing on success (errors are still delivered)
    """
    status = run_info.get("status", "")
    summary = (run_info.get("output_summary") or "").strip()
    error_message = (run_info.get("error_message") or "").strip()

    # Paused runs notify through workflow_wait_for_user; skipped runs are
    # internal scheduler bookkeeping and should not bother the user.
    if status in {"paused", "skipped"}:
        return

    # ── Load workflow to get deliver + origin + notify ────────────────
    try:
        try:
            from .db import get_db
        except ImportError:
            from db import get_db  # noqa: E402
        db = get_db()
        wf = db.get_workflow(workflow_id)
    except Exception:
        logger.debug("Cannot load workflow for delivery: %s", workflow_id, exc_info=True)
        return

    if wf is None:
        return

    deliver = (wf.get("deliver") or "").strip()
    if deliver == "local" or not deliver:
        return  # No gateway delivery needed

    # ── Build the message according to status + notify mode ───────────
    name = wf.get("name") or workflow_id
    notify = (wf.get("notify") or "summary").strip().lower()

    if status == "error":
        if not error_message:
            return
        message = f"⚠️ Workflow *{name}* failed\n\n{error_message}"
    else:  # success
        if notify == "silent":
            return
        if notify == "minimal":
            message = f"✅ Workflow *{name}* completed"
        else:  # "summary" (default)
            if not summary:
                return
            message = f"✅ Workflow *{name}* completed\n\n{summary}"

    origin = wf.get("origin") or {}

    # ── Resolve target platform + chat_id ─────────────────────────────
    if deliver == "origin":
        target_platform = (origin.get("platform") or "").strip()
        target_chat_id = (origin.get("chat_id") or "").strip()
    else:
        target_platform = deliver
        target_chat_id = (origin.get("chat_id") or "").strip()
        # If deliver specifies a platform but origin doesn't have chat_id,
        # try env vars (mirrors cron's home-target resolution)
        if not target_chat_id:
            target_chat_id = _resolve_home_chat_id(target_platform)

    if not target_platform or not target_chat_id:
        logger.debug(
            "Workflow '%s': no delivery target resolved (deliver=%s, origin=%s)",
            workflow_id, deliver, origin,
        )
        return

    # ── Send via gateway adapter ──────────────────────────────────────
    _send_via_gateway(
        target_platform,
        str(target_chat_id),
        message,
        thread_id=(origin.get("thread_id") or "").strip() or None,
    )


def _resolve_home_chat_id(platform_name: str) -> str:
    """Resolve home chat/channel ID from environment variables for a platform.

    Mirrors cron's _get_home_target_chat_id for common platforms.
    """
    env_map = {
        "telegram": "TELEGRAM_HOME_CHANNEL",
        "discord": "DISCORD_HOME_CHANNEL",
        "slack": "SLACK_HOME_CHANNEL",
        "matrix": "MATRIX_HOME_ROOM",
        "signal": "SIGNAL_HOME_CHANNEL",
        "email": "EMAIL_HOME_ADDRESS",
        "whatsapp": "WHATSAPP_HOME_CHANNEL",
    }
    env_var = env_map.get(platform_name.lower(), "")
    if not env_var:
        return ""
    return os.environ.get(env_var, "")


def _send_via_gateway(
    platform_str: str,
    chat_id: str,
    message: str,
    thread_id: Optional[str] = None,
) -> None:
    """Thin wrapper kept for call-site readability — see send_gateway_message."""
    send_gateway_message(platform_str, chat_id, message, thread_id=thread_id)


def send_gateway_message(
    platform_str: Optional[str],
    chat_id: Optional[str],
    message: str,
    thread_id: Optional[str] = None,
) -> bool:
    """Deliver a message to a chat through the live Hermes gateway.

    Resolves the running GatewayRunner, finds the adapter for the target
    platform, and schedules the async send on the gateway event loop. Never
    raises and never blocks that loop; genuine send failures are surfaced via
    a logged done-callback instead of being silently swallowed.

    Returns True when the send was scheduled onto a live adapter, False when
    no gateway/adapter/loop is available to deliver it.
    """
    import asyncio

    if not platform_str or not chat_id:
        return False

    gateway = _resolve_gateway()
    if gateway is None:
        logger.warning(
            "Workflow message to %s:%s dropped — no live gateway "
            "(is the Hermes gateway running?)",
            platform_str, chat_id,
        )
        return False

    loop = _running_gateway_loop(gateway)
    if loop is None:
        logger.warning(
            "Workflow message to %s:%s dropped — gateway event loop not running",
            platform_str, chat_id,
        )
        return False

    adapter = _find_adapter(gateway, platform_str)
    if adapter is None:
        logger.warning(
            "Workflow message to %s:%s dropped — no connected adapter for "
            "platform '%s'",
            platform_str, chat_id, platform_str,
        )
        return False

    async def _send() -> Any:
        metadata = {"thread_id": thread_id} if thread_id else None
        try:
            return await adapter.send(str(chat_id), message, metadata=metadata)
        except TypeError:
            # Older adapters don't accept a metadata kwarg.
            return await adapter.send(str(chat_id), message)

    def _log_outcome(get_result) -> None:
        try:
            result = get_result()
        except Exception:
            logger.warning(
                "Workflow message to %s:%s failed to send",
                platform_str, chat_id, exc_info=True,
            )
            return
        failed = (
            result.get("success") is False if isinstance(result, dict)
            else getattr(result, "success", True) is False
        )
        if failed:
            error = (
                result.get("error") if isinstance(result, dict)
                else getattr(result, "error", None)
            )
            logger.warning(
                "Workflow message to %s:%s rejected by adapter: %s",
                platform_str, chat_id, error,
            )

    try:
        # Schedule on the gateway loop. If we're already running on that loop
        # (e.g. invoked from within the pre_gateway_dispatch hook), create the
        # task directly; otherwise hand it across threads. Either way we never
        # block the loop waiting on its own coroutine.
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        if current is loop:
            task = loop.create_task(_send())
            task.add_done_callback(lambda t: _log_outcome(t.result))
        else:
            future = asyncio.run_coroutine_threadsafe(_send(), loop)
            future.add_done_callback(lambda f: _log_outcome(f.result))
        return True
    except Exception:
        logger.warning(
            "Workflow message to %s:%s could not be scheduled",
            platform_str, chat_id, exc_info=True,
        )
        return False


# ── Timeout checker ───────────────────────────────────────────────────────

_last_timeout_check: float = 0.0
_TIMEOUT_INTERVAL = 30  # seconds


def _run_timeout_checker() -> None:
    """Check for expired pending actions every 30 seconds."""
    global _last_timeout_check
    now = time.time()
    if now - _last_timeout_check < _TIMEOUT_INTERVAL:
        return
    _last_timeout_check = now

    try:
        try:
            from .db import get_db
        except ImportError:
            from db import get_db  # noqa: E402
        db = get_db()
        expired = db.get_expired_actions()

        for action in expired:
            db.expire_action(action["id"])
            wf_id = action["workflow_id"]
            logger.info(
                "Timeout: action %s for workflow '%s' expired — triggering immediately",
                action["id"],
                wf_id,
            )
            trigger_workflow_now(wf_id)

        if expired:
            logger.info("Timeout checker: auto-expired %d pending action(s)", len(expired))
    except Exception:
        logger.exception("Timeout checker failed")


# ── Immediate trigger —────────────────────────────────────────────────────

def trigger_workflow_now(workflow_id: str) -> Optional[Dict[str, Any]]:
    """
    Trigger a workflow execution immediately, bypassing the cron schedule.

    Called when a human responds to a pending action.  Overlap protection
    still applies: if a run is already active (paused), skip.

    Returns the run_info dict from executor.execute(), or None on failure.
    """
    if _executor is None:
        logger.warning("trigger_workflow_now for '%s' but no executor registered", workflow_id)
        return None

    logger.info("Immediate trigger: %s", workflow_id)
    try:
        run_info = _executor(workflow_id)

        if isinstance(run_info, dict):
            _deliver_workflow_output(workflow_id, run_info)
            _auto_disable_oneshot(workflow_id, run_info)

        return run_info
    except Exception:
        logger.exception("Unhandled error in immediate trigger for workflow '%s'", workflow_id)
        return None


def trigger_workflow_now_async(workflow_id: str) -> None:
    """Resume a workflow off the caller's thread.

    The resume paths (chat reply, /workflows respond, workflow_submit_response)
    all fire from the gateway's asyncio event-loop thread. Running a full agent
    conversation there would block the entire gateway — and any gateway send the
    workflow itself schedules on that same loop — until the run finishes. Hand the
    work to a short-lived daemon thread so the caller returns immediately; the
    workflow then delivers its own output through the normal gateway path.
    """
    threading.Thread(
        target=trigger_workflow_now,
        args=(workflow_id,),
        name=f"workflow-resume-{workflow_id}",
        daemon=True,
    ).start()


def _auto_disable_oneshot(workflow_id: str, run_info: Optional[Dict[str, Any]] = None) -> None:
    """If a workflow is a one-shot, disable it after it fires."""
    status = (run_info or {}).get("status")
    if status in {"paused", "running", "skipped"}:
        return
    try:
        try:
            from .db import get_db
        except ImportError:
            from db import get_db  # noqa: E402
        db = get_db()
        wf = db.get_workflow(workflow_id)
        if wf and wf.get("trigger_type") == "oneshot" and wf.get("enabled"):
            logger.info("Auto-disabling one-shot workflow: %s", workflow_id)
            updated = db.update_workflow(workflow_id, enabled=False)
            if updated is not None:
                unschedule_workflow(workflow_id)
    except Exception:
        logger.exception("Failed to auto-disable one-shot workflow '%s'", workflow_id)


# ── Lifecycle ─────────────────────────────────────────────────────────────

def start(workflows: list) -> None:
    """Start the poll loop daemon thread and schedule all enabled workflows."""
    global _thread
    if _thread is not None and _thread.is_alive():
        logger.warning("Scheduler already running")
        return

    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="workflow-scheduler")
    _thread.start()
    schedule_all_workflows(workflows)
    logger.info("Workflow scheduler started (poll loop + %d workflows)", len(workflows))


def shutdown() -> None:
    """Gracefully stop the poll loop and cancel all one-shot timers."""
    _stop_event.set()
    with _lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()
    logger.info("Workflow scheduler shut down")


def get_jobs_status() -> list:
    """Return a list of scheduled workflow info dicts for the dashboard API."""
    jobs: list = []
    with _lock:
        now = time.time()
        for wf_id, next_ts in _next_runs.items():
            wf = _workflows.get(wf_id, {})
            jobs.append({
                "id": f"workflow:{wf_id}",
                "name": wf.get("name", wf_id),
                "next_run": datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat(),
            })
        for wf_id, timer in _timers.items():
            jobs.append({
                "id": f"workflow:{wf_id}",
                "name": f"One-shot: {wf_id}",
                "next_run": "pending (timer)",
            })
    return jobs
