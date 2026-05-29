"""
Workflow Engine — Data Models.

Dataclass definitions for the core entities:
- Workflow: a named, scheduled agent job with its own prompt and state
- WorkflowRun: a single execution of a workflow
- PendingAction: a human-in-the-loop question raised during a run
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Workflow:
    """A scheduled workflow — the central entity."""

    id: str
    name: str
    cron_expression: str
    prompt: str
    description: str = ""
    enabled: bool = True
    origin: Optional[Dict[str, Any]] = None
    trigger_type: str = "cron"  # "cron" | "oneshot"
    deliver: str = "local"  # "local" | "telegram" | "discord" | "slack" | "email" | "origin"
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "cron_expression": self.cron_expression,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "origin": self.origin,
            "trigger_type": self.trigger_type,
            "deliver": self.deliver,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Workflow":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            cron_expression=row["cron_expression"],
            prompt=row["prompt"],
            enabled=bool(row.get("enabled", True)),
            origin=row.get("origin"),
            trigger_type=row.get("trigger_type", "cron"),
            deliver=row.get("deliver", "local"),
            created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0),
        )


@dataclass
class WorkflowRun:
    """A single execution tick of a workflow."""

    id: str
    workflow_id: str
    status: str  # running | success | paused | error
    started_at: float = 0.0
    finished_at: Optional[float] = None
    pending_action_id: Optional[str] = None
    error_message: Optional[str] = None
    output_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pending_action_id": self.pending_action_id,
            "error_message": self.error_message,
            "output_summary": self.output_summary,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "WorkflowRun":
        return cls(
            id=row["id"],
            workflow_id=row["workflow_id"],
            status=row["status"],
            started_at=row.get("started_at", 0.0),
            finished_at=row.get("finished_at"),
            pending_action_id=row.get("pending_action_id"),
            error_message=row.get("error_message"),
            output_summary=row.get("output_summary"),
        )


@dataclass
class PendingAction:
    """A human-in-the-loop question raised during a workflow run.

    The agent calls ``workflow_wait_for_user(question)`` to pause and ask
    the human a freeform question.  The human replies directly in the chat
    where the workflow was created (captured via ``pre_gateway_dispatch``)
    or via the ``/workflows respond`` slash command.
    """

    id: str
    workflow_id: str
    question: str
    status: str = "pending"  # pending | resolved | dismissed
    run_id: Optional[str] = None
    context: Optional[str] = None
    response: Optional[str] = None
    created_at: float = 0.0
    responded_at: Optional[float] = None

    # ── Timeout ──────────────────────────────────────────────────────
    expires_at: Optional[float] = None
    timeout_status: Optional[str] = None  # None | "expired"

    # ── Origin (for chat-based reply capture via pre_gateway_dispatch) ─
    origin_platform: Optional[str] = None  # "telegram", "discord", etc.
    origin_chat_id: Optional[str] = None   # chat/room/dm id
    origin_thread_id: Optional[str] = None  # forum topic / thread id
    origin_user_id: Optional[str] = None   # user who created the workflow

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "question": self.question,
            "context": self.context,
            "status": self.status,
            "response": self.response,
            "created_at": self.created_at,
            "responded_at": self.responded_at,
            "expires_at": self.expires_at,
            "timeout_status": self.timeout_status,
            "origin_platform": self.origin_platform,
            "origin_chat_id": self.origin_chat_id,
            "origin_thread_id": self.origin_thread_id,
            "origin_user_id": self.origin_user_id,
        }
        return result

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "PendingAction":
        return cls(
            id=row["id"],
            workflow_id=row["workflow_id"],
            question=row["question"],
            status=row.get("status", "pending"),
            run_id=row.get("run_id"),
            context=row.get("context"),
            response=row.get("response"),
            created_at=row.get("created_at", 0.0),
            responded_at=row.get("responded_at"),
            expires_at=row.get("expires_at"),
            timeout_status=row.get("timeout_status"),
            origin_platform=row.get("origin_platform"),
            origin_chat_id=row.get("origin_chat_id"),
            origin_thread_id=row.get("origin_thread_id"),
            origin_user_id=row.get("origin_user_id"),
        )
