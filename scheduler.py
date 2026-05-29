"""
Workflow Engine — Scheduler.

Manages the APScheduler-based cron loop that triggers workflow
executions. Handles:
- Loading all enabled workflows on startup and scheduling them
- Adding/removing jobs when workflows are created/deleted/enabled/disabled
- Preventing overlapping runs (won't start a new run if the previous
  is still in "paused" state with an unresolved pending action)
- Dispatching each tick to the executor
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ── Lazy import apscheduler — degrade gracefully if not installed ─────────

_apscheduler_missing: Optional[str] = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: F811
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.jobstores.base import JobLookupError

    _HAS_APSCHEDULER = True
except ImportError as e:
    _HAS_APSCHEDULER = False
    _apscheduler_missing = (
        f"APScheduler is not installed ({e}). "
        f"Install it with: pip install apscheduler"
    )
    # Define stubs so the module can still be imported
    BackgroundScheduler = None  # type: ignore[misc]
    CronTrigger = None  # type: ignore[misc]
    JobLookupError = Exception  # type: ignore[misc]

# Reference to the executor — set at registration time
_executor: Optional[Callable[[str], None]] = None


def set_executor(fn: Callable[[str], None]) -> None:
    """Register the executor function the scheduler will call on each tick."""
    global _executor
    _executor = fn


# ── Scheduler singleton ───────────────────────────────────────────────────

_scheduler: Optional[BackgroundScheduler] = None


def get_scheduler() -> BackgroundScheduler:
    """Get or create the BackgroundScheduler singleton."""
    if not _HAS_APSCHEDULER:
        raise RuntimeError(_apscheduler_missing)

    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,  # if a job is missed, run it once, not multiple times
            "max_instances": 1,  # prevent concurrent executions of the same job
            "misfire_grace_time": 300,  # 5 minutes grace for missed jobs
        },
    )
    return _scheduler


# ── Job ID helpers ────────────────────────────────────────────────────────


def _job_id(workflow_id: str) -> str:
    return f"workflow:{workflow_id}"


# ── Schedule / unschedule ─────────────────────────────────────────────────


def schedule_workflow(workflow: Dict[str, Any]) -> None:
    """
    Add or update a cron job for a given workflow.
    The job will call the executor with the workflow_id.

    Supports both cron-based (recurring) and one-shot (DateTrigger) workflows.
    One-shot workflows are auto-disabled after firing.
    """
    if not _HAS_APSCHEDULER:
        logger.warning("Cannot schedule workflow '%s': %s", workflow.get("id", "?"), _apscheduler_missing)
        return
    scheduler = get_scheduler()
    jid = _job_id(workflow["id"])

    # Remove existing job for this workflow if present
    try:
        scheduler.remove_job(jid)
    except JobLookupError:
        pass

    if not workflow.get("enabled", True):
        logger.debug("Workflow %s is disabled — not scheduling", workflow["id"])
        return

    trigger_type = workflow.get("trigger_type", "cron")

    if trigger_type == "oneshot":
        # Use DateTrigger for one-shot workflows
        try:
            from apscheduler.triggers.date import DateTrigger
            from datetime import datetime, timezone

            # Parse the cron expression back to a datetime
            # The cron was generated as "minute hour day month *"
            parts = workflow["cron_expression"].split()
            if len(parts) == 5:
                run_time = datetime(
                    year=datetime.now(timezone.utc).year,
                    month=int(parts[3]),
                    day=int(parts[2]),
                    hour=int(parts[1]),
                    minute=int(parts[0]),
                )
            else:
                logger.error("Invalid one-shot cron for %s: %s", workflow["id"], workflow["cron_expression"])
                return

            # If the computed time is in the past, it'll fire immediately
            trigger = DateTrigger(run_date=run_time)
        except ImportError:
            logger.warning("DateTrigger not available, falling back to cron for '%s'", workflow["id"])
            try:
                trigger = CronTrigger.from_crontab(workflow["cron_expression"])
            except (ValueError, KeyError) as e:
                logger.error("Invalid cron for '%s': %s — %s", workflow["id"], workflow["cron_expression"], e)
                return
        except Exception as e:
            logger.error("Failed to create DateTrigger for '%s': %s", workflow["id"], e)
            return
    else:
        try:
            trigger = CronTrigger.from_crontab(workflow["cron_expression"])
        except (ValueError, KeyError) as e:
            logger.error(
                "Invalid cron expression for workflow %s: %s — %s",
                workflow["id"],
                workflow["cron_expression"],
                e,
            )
            return

    scheduler.add_job(
        _tick,
        trigger=trigger,
        id=jid,
        args=[workflow["id"]],
        name=f"Workflow: {workflow['name']}",
        replace_existing=True,
    )

    # Compute next run time for logging
    job = scheduler.get_job(jid)
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else "unknown"

    logger.info(
        "Scheduled workflow '%s' (%s): type=%s cron='%s' next_run=%s",
        workflow["name"],
        workflow["id"],
        trigger_type,
        workflow["cron_expression"],
        next_run,
    )


def unschedule_workflow(workflow_id: str) -> None:
    """Remove a workflow's cron job from the scheduler."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(_job_id(workflow_id))
        logger.info("Unscheduled workflow: %s", workflow_id)
    except JobLookupError:
        pass


def schedule_all_workflows(workflows: list) -> None:
    """Schedule all enabled workflows. Called on startup."""
    for wf in workflows:
        schedule_workflow(wf)
    logger.info("Scheduled %d workflows", len(workflows))


# ── Tick — called by APScheduler when a cron trigger fires ─────────────────


def _tick(workflow_id: str) -> None:
    """
    Called by APScheduler when a workflow's cron trigger fires.

    Dispatches to the executor. If no executor is registered, logs
    a warning and skips.  After execution, auto-disables one-shot workflows.
    """
    if _executor is None:
        logger.warning(
            "Scheduler tick for workflow '%s' but no executor registered — skipping",
            workflow_id,
        )
        return

    logger.info("Workflow tick: %s", workflow_id)
    try:
        run_info = _executor(workflow_id)

        # Auto-disable one-shot workflows after they fire
        if isinstance(run_info, dict):
            _auto_disable_oneshot(workflow_id)

    except Exception:
        logger.exception("Unhandled error in executor for workflow '%s'", workflow_id)


# ═══════════════════════════════════════════════════════════════════════════
# Timeout checker & immediate trigger — human-in-the-loop support
# ═══════════════════════════════════════════════════════════════════════════

# Reference to the gateway runner — set via set_gateway_ref() so the
# pre_gateway_dispatch hook and timeout checker can access platform adapters.
_gateway_ref: Any = None


def set_gateway_ref(gateway: Any) -> None:
    """Store a reference to the GatewayRunner for message delivery."""
    global _gateway_ref
    _gateway_ref = gateway


def trigger_workflow_now(workflow_id: str) -> Optional[Dict[str, Any]]:
    """
    Trigger a workflow execution immediately, bypassing the cron schedule.

    Called when a human responds to a pending action — we don't want to
    wait for the next cron tick. The overlap protection still applies:
    if a run is already active (another pending action unresolved), skip.

    Returns the run_info dict from executor.execute(), or None on failure.
    Auto-disables one-shot workflows after execution.
    """
    if _executor is None:
        logger.warning(
            "trigger_workflow_now for '%s' but no executor registered",
            workflow_id,
        )
        return None

    logger.info("Immediate trigger: %s", workflow_id)
    try:
        run_info = _executor(workflow_id)

        # Auto-disable one-shot workflows after they fire
        if isinstance(run_info, dict):
            _auto_disable_oneshot(workflow_id)

        return run_info
    except Exception:
        logger.exception(
            "Unhandled error in immediate trigger for workflow '%s'",
            workflow_id,
        )
        return None


def _auto_disable_oneshot(workflow_id: str) -> None:
    """
    If a workflow is a one-shot, disable it after it fires so it doesn't
    repeat on the equivalent cron.
    """
    try:
        from .db import get_db
        db = get_db()
        wf = db.get_workflow(workflow_id)
        if wf and wf.get("trigger_type") == "oneshot" and wf.get("enabled"):
            logger.info("Auto-disabling one-shot workflow: %s", workflow_id)
            updated = db.update_workflow(workflow_id, enabled=False)
            if updated is not None:
                unschedule_workflow(workflow_id)
    except Exception:
        logger.exception(
            "Failed to auto-disable one-shot workflow '%s'",
            workflow_id,
        )


def _timeout_checker() -> None:
    """
    Periodically check for expired pending actions and auto-dismiss them.

    When an action expires, the associated workflow is triggered immediately
    so the agent can continue without the human's input.
    """
    try:
        from .db import get_db
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


def shutdown() -> None:
    """Gracefully shut down the scheduler. Called by plugin_shutdown hook."""
    if _scheduler is not None and getattr(_scheduler, "running", False):
        _scheduler.shutdown(wait=False)
        logger.info("Workflow scheduler shut down")


def get_jobs_status() -> list:
    """Return a list of scheduled job info dicts for the dashboard API."""
    if not _HAS_APSCHEDULER or _scheduler is None:
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return jobs


# ── Scheduler lifecycle ───────────────────────────────────────────────────


def start(workflows: list) -> None:
    """
    Start the scheduler and schedule all enabled workflows.
    Call once during plugin registration.
    """
    if not _HAS_APSCHEDULER:
        logger.warning(
            "Workflow scheduler NOT started: %s",
            _apscheduler_missing,
        )
        return
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("Workflow scheduler started")
    schedule_all_workflows(workflows)

    # Add the timeout checker — runs every 30 seconds
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        sched.add_job(
            _timeout_checker,
            trigger=IntervalTrigger(seconds=30),
            id="workflow:__timeout_checker__",
            name="Workflow timeout checker",
            replace_existing=True,
        )
        logger.info("Timeout checker scheduled (every 30s)")
    except Exception:
        logger.warning("Could not schedule timeout checker", exc_info=True)


def shutdown(wait: bool = True) -> None:
    """Shut down the scheduler. Call on plugin teardown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=wait)
        _scheduler = None
        logger.info("Workflow scheduler shut down")


def get_jobs_status() -> list:
    """Return the status of all scheduled jobs for the dashboard."""
    if not _HAS_APSCHEDULER:
        return []
    sched = get_scheduler()
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return jobs
