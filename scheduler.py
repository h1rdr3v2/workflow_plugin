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
        "Scheduled workflow '%s' (%s): cron='%s' next_run=%s",
        workflow["name"],
        workflow["id"],
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
    a warning and skips.
    """
    if _executor is None:
        logger.warning(
            "Scheduler tick for workflow '%s' but no executor registered — skipping",
            workflow_id,
        )
        return

    logger.info("Workflow tick: %s", workflow_id)
    try:
        _executor(workflow_id)
    except Exception:
        logger.exception("Unhandled error in executor for workflow '%s'", workflow_id)


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
