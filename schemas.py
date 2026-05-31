"""
Workflow Engine — Tool Schemas.

Each schema follows the Hermes convention: name, description, parameters.
These are read by the LLM to decide when and how to call each tool.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════
# 1. workflow_create
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_CREATE = {
    "name": "workflow_create",
    "description": (
        "Create a new scheduled workflow. A workflow is a cron-like job that "
        "runs on a schedule, has its own persistent state, and can pause for "
        "human input when needed.\n\n"
        "Use this when a user asks you to set up an automated recurring task — "
        "like a daily report, a rotation manager, a monitoring check, or any "
        "job that should run on a fixed schedule.\n\n"
        "IMPORTANT: The workflow_id must be a unique slug (lowercase, "
        "underscores, no spaces). The cron_expression is a standard 5-field "
        "cron string (e.g., '0 9 * * 1-5' for weekdays at 9am). The prompt "
        "should be a clear system instruction telling the agent what to do "
        "on each run.\n\n"
        "For ONE-SHOT workflows, provide trigger_at (ISO timestamp) or "
        "trigger_in (duration like '5m', '1h', '30s') instead of a "
        "cron_expression. One-shot workflows auto-disable after firing.\n\n"
        "Provide 'origin' to enable chat-based reply capture: "
        '{"platform": "discord", "chat_id": "123", "thread_id": "456"}.'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "Unique slug for this workflow. Use lowercase, underscores, no spaces. E.g., 'daily_standup_reminder' or 'pr_review_rotation'.",
            },
            "name": {
                "type": "string",
                "description": "Human-readable name for the workflow. E.g., 'Daily Standup Reminder'.",
            },
            "cron_expression": {
                "type": "string",
                "description": "Standard 5-field cron expression. E.g., '0 9 * * 1-5' (weekdays at 9am), '*/30 * * * *' (every 30 minutes), '0 0 1 * *' (midnight on the 1st of each month). NOT required if trigger_at or trigger_in is provided.",
            },
            "prompt": {
                "type": "string",
                "description": "System prompt / instructions for the agent on each run. Describe what the agent should do step-by-step. Include reminders to use workflow_load_state at the start and workflow_save_state before finishing.",
            },
            "description": {
                "type": "string",
                "description": "Optional: a short description of what this workflow does. Shown in the dashboard.",
            },
            "trigger_at": {
                "type": "string",
                "description": "Optional: ISO 8601 timestamp for a ONE-SHOT run. E.g., '2026-05-29T14:30:00Z'. The workflow fires once at this time then auto-disables.",
            },
            "trigger_in": {
                "type": "string",
                "description": "Optional: duration string for a ONE-SHOT run. E.g., '5m', '1h', '30s', '2d'. The workflow fires once after this delay then auto-disables.",
            },
            "origin": {
                "type": "object",
                "description": "Optional: where this workflow was created. Enables chat-based reply capture. E.g., {'platform': 'discord', 'chat_id': '123456'}. Fields: platform, chat_id, thread_id, user_id (all optional strings).",
                "properties": {
                    "platform": {"type": "string", "description": "Platform name: 'discord', 'telegram', 'slack', etc."},
                    "chat_id": {"type": "string", "description": "Chat/channel/DM ID where responses are captured."},
                    "thread_id": {"type": "string", "description": "Optional: thread/forum topic ID."},
                    "user_id": {"type": "string", "description": "Optional: user who created the workflow."},
                },
            },
            "deliver": {
                "type": "string",
                "description": "Where to deliver workflow output and messages. 'local', 'discord', 'telegram', 'slack', 'email', or 'origin' (agent-only — uses creation context).",
                "enum": ["local", "discord", "telegram", "slack", "email", "origin"],
            },
            "notify": {
                "type": "string",
                "description": (
                    "How much to tell the user when a run finishes successfully. "
                    "'summary' (default): deliver the agent's final summary. "
                    "'minimal': deliver only a static '✅ Workflow <name> completed' line "
                    "— best for side-effect jobs where the result isn't a message. "
                    "'silent': deliver nothing on success (errors are still reported). "
                    "Pick 'summary' for workflows whose output IS the message (e.g. a daily digest)."
                ),
                "enum": ["summary", "minimal", "silent"],
            },
        },
        "required": ["workflow_id", "name", "prompt", "deliver"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 2. workflow_update
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_UPDATE = {
    "name": "workflow_update",
    "description": (
        "Update an existing workflow — change its schedule, prompt, name, "
        "origin, or enable/disable it. Only the workflow_id is required; all other "
        "fields are optional and only updated if provided.\n\n"
        "Use this when a user wants to modify a workflow's behavior, pause "
        "it temporarily, or change its run frequency."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The ID of the workflow to update.",
            },
            "name": {
                "type": "string",
                "description": "Optional: new human-readable name.",
            },
            "description": {
                "type": "string",
                "description": "Optional: new description.",
            },
            "cron_expression": {
                "type": "string",
                "description": "Optional: new cron expression.",
            },
            "prompt": {
                "type": "string",
                "description": "Optional: new system prompt.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Optional: set to true to enable, false to pause/disable.",
            },
            "origin": {
                "type": "object",
                "description": "Optional: update the origin platform/chat info for reply capture.",
                "properties": {
                    "platform": {"type": "string"},
                    "chat_id": {"type": "string"},
                    "thread_id": {"type": "string"},
                    "user_id": {"type": "string"},
                },
            },
            "deliver": {
                "type": "string",
                "description": "Optional: update delivery target. 'local', 'discord', 'telegram', 'slack', 'email', or 'origin' (agent-only).",
                "enum": ["local", "discord", "telegram", "slack", "email", "origin"],
            },
            "notify": {
                "type": "string",
                "description": "Optional: update success-notification verbosity. 'summary' (deliver the run summary), 'minimal' (static completion line), or 'silent' (nothing on success; errors still reported).",
                "enum": ["summary", "minimal", "silent"],
            },
        },
        "required": ["workflow_id"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 3. workflow_delete
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_DELETE = {
    "name": "workflow_delete",
    "description": (
        "Permanently delete a workflow and all its saved state and pending "
        "actions. This cannot be undone.\n\n"
        "Use this when a user wants to remove an automated workflow entirely."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The ID of the workflow to delete.",
            },
        },
        "required": ["workflow_id"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 4. workflow_list
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_LIST = {
    "name": "workflow_list",
    "description": (
        "List all workflows. Returns each workflow's id, name, schedule, "
        "enabled status, and next scheduled run time.\n\n"
        "Use this when a user asks 'what workflows do I have?' or 'show me "
        "my automations'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "enabled_only": {
                "type": "boolean",
                "description": "If true, only return enabled workflows. Default: false (show all).",
            },
        },
        "required": [],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 5. workflow_get
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_GET = {
    "name": "workflow_get",
    "description": (
        "Get full details of a single workflow including its definition, "
        "saved state keys, and any pending human-input requests.\n\n"
        "Use this when a user asks about a specific workflow, or when you "
        "need to inspect a workflow's configuration before modifying it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The ID of the workflow to inspect.",
            },
        },
        "required": ["workflow_id"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 6. workflow_save_state
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_SAVE_STATE = {
    "name": "workflow_save_state",
    "description": (
        "Save a persistent value for the CURRENT workflow. This value will "
        "survive across runs and be available the next time this workflow "
        "executes.\n\n"
        "CRITICAL: Always save state before completing a workflow run if "
        "you made decisions the next run needs to know about (assignments, "
        "counters, rotation state, last-run summary, etc.).\n\n"
        "The workflow_id is automatically inferred from the current "
        "execution context — you only need to provide key and value."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "A descriptive key for this state value. Use names like 'assignment_rotation', 'last_run_summary', 'review_queue', etc.",
            },
            "value": {
                "type": "string",
                "description": "The value to store, serialized as a JSON string. For complex data, use json.dumps(). E.g., '{\"last\": \"alice\", \"history\": [\"bob\", \"carol\"]}'.",
            },
        },
        "required": ["key", "value"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 7. workflow_load_state
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_LOAD_STATE = {
    "name": "workflow_load_state",
    "description": (
        "Retrieve a previously saved state value for the CURRENT workflow. "
        "Use this at the START of every workflow run to understand what "
        "happened in prior executions.\n\n"
        "Returns the stored value (deserialized from JSON), or an indicator "
        "that the key wasn't found. Always handle missing keys by "
        "initializing sensible defaults.\n\n"
        "The workflow_id is automatically inferred — you only need the key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The key to retrieve. Same key used in workflow_save_state.",
            },
        },
        "required": ["key"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 8. workflow_delete_state
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_DELETE_STATE = {
    "name": "workflow_delete_state",
    "description": (
        "Delete a state key for the CURRENT workflow. Use this to clean up "
        "old or obsolete state data.\n\n"
        "The workflow_id is automatically inferred — you only need the key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "The state key to delete.",
            },
        },
        "required": ["key"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 9. workflow_wait_for_user
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_WAIT_FOR_USER = {
    "name": "workflow_wait_for_user",
    "description": (
        "Pause the CURRENT workflow and wait for a human to provide input. "
        "Use this when you need approval, clarification, or a decision only "
        "a human can make.\n\n"
        "The human can reply directly in the chat where this workflow was "
        "created, or use the /workflows slash command. Their freeform text "
        "response will be captured automatically.\n\n"
        "Optionally set max_wait_seconds to auto-continue if the human "
        "doesn't respond in time.\n\n"
        "CRITICAL: After calling this, you MUST end the current run. The "
        "workflow will resume immediately once the human responds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The specific question or decision the human needs to answer. Be clear and actionable. Example: 'Should I escalate this alert? The error rate is 4.7% which is above the 3% threshold.'",
            },
            "context": {
                "type": "string",
                "description": "Optional: additional context to help the human understand the situation. Can be a JSON string with structured data or plain text.",
            },
            "max_wait_seconds": {
                "type": "integer",
                "description": (
                    "Optional: maximum time in seconds to wait for human input. "
                    "If the human doesn't respond within this time, the pending "
                    "action is auto-dismissed and the workflow continues on its "
                    "next tick with a timeout notice. Omit for no timeout."
                ),
            },
        },
        "required": ["question"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 10. workflow_submit_response
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_SUBMIT_RESPONSE = {
    "name": "workflow_submit_response",
    "description": (
        "Submit a human's response to a previously paused workflow question. "
        "This resolves the pending action so the agent sees the response on "
        "its next scheduled run.\n\n"
        "Typically called by the human via the /workflows command or "
        "dashboard, NOT by the agent during a workflow run."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": {
                "type": "string",
                "description": "The ID of the pending action to respond to.",
            },
            "response": {
                "type": "string",
                "description": "The human's answer, decision, or instruction.",
            },
        },
        "required": ["action_id", "response"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 11. workflow_list_pending
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_LIST_PENDING = {
    "name": "workflow_list_pending",
    "description": (
        "List all workflows that are currently waiting for human input. "
        "Returns each pending question with its workflow_id, action_id, "
        "and when it was created.\n\n"
        "Use this at the start of a workflow run to check if there are "
        "outstanding human questions, or use it as a human to see what "
        "needs your attention. Filter by workflow_id to see only a "
        "specific workflow's pending items."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "Optional: filter to show pending actions for a specific workflow only.",
            },
        },
        "required": [],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 12. workflow_send_message
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_SEND_MESSAGE = {
    "name": "workflow_send_message",
    "description": (
        "Send a one-way status update or result to the workflow's chat "
        "mid-run, without pausing.\n\n"
        "DO NOT use this to ask a question that needs an answer — use "
        "workflow_wait_for_user, which delivers the question to the user "
        "ITSELF. Sending the question here first and then calling "
        "workflow_wait_for_user posts it to the user twice.\n\n"
        "Only works if the workflow has an 'origin'/'deliver' target "
        "configured. The workflow_id is inferred automatically — you only "
        "need to provide the message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to send to the originating chat. Can include markdown formatting.",
            },
        },
        "required": ["message"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 13. workflow_disable
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_DISABLE = {
    "name": "workflow_disable",
    "description": (
        "Disable/pause a workflow so it stops running on its schedule. "
        "The workflow will not fire again until re-enabled with "
        "workflow_enable. The current run (if any) will complete.\n\n"
        "Use this when a workflow has completed its task, should be "
        "suspended temporarily, or when a one-shot workflow finishes. "
        "A workflow CAN disable itself — just pass its own workflow_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The ID of the workflow to disable/pause.",
            },
        },
        "required": ["workflow_id"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# 14. workflow_enable
# ═══════════════════════════════════════════════════════════════════════════

WORKFLOW_ENABLE = {
    "name": "workflow_enable",
    "description": (
        "Re-enable a previously disabled/paused workflow so it resumes "
        "running on its regular schedule.\n\n"
        "Use this to restart a workflow that was paused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The ID of the workflow to re-enable.",
            },
        },
        "required": ["workflow_id"],
    },
}