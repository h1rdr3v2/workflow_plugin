"""
Tool schemas for the Workflow Engine plugin.

These schemas are read by the LLM to decide when to call each tool.
Each follows the Hermes convention: name, description, parameters.
"""

# ── workflow_save_state ───────────────────────────────────────────────────

WORKFLOW_SAVE_STATE = {
    "name": "workflow_save_state",
    "description": (
        "Save a persistent state value that will survive across cron job "
        "executions and agent restarts. Use this to remember decisions, "
        "assignments, counters, rotation history, or any data that needs "
        "to carry over to the next run. The value can be any JSON-serializable "
        "object (string, number, list, dict, boolean). Overwrites any "
        "existing value for the same key.\n\n"
        "CRITICAL: Always call this before completing a cron job if you "
        "have made decisions that the next run should know about. Without "
        "this, every cron execution starts from a blank slate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "A unique key identifying this state. Use namespaced keys "
                    "like 'assignment_rotation', 'last_run_summary', or "
                    "'review_queue.v2' to keep state organized across workflows."
                ),
            },
            "value": {
                "type": "string",
                "description": (
                    "The value to store, serialized as a JSON string. Pass "
                    "complex structures as a JSON-encoded string "
                    "(e.g., '{\"last\": \"alice\", \"history\": [\"bob\", \"carol\"]}'). "
                    "The value will be deserialized back to its original form "
                    "when loaded."
                ),
            },
        },
        "required": ["key", "value"],
    },
}

# ── workflow_load_state ───────────────────────────────────────────────────

WORKFLOW_LOAD_STATE = {
    "name": "workflow_load_state",
    "description": (
        "Retrieve a previously saved workflow state value. Use this at the "
        "START of every cron job to understand what happened in prior runs — "
        "who was assigned last time, what decisions were made, what the "
        "current rotation state is, etc.\n\n"
        "Returns the stored value (deserialized), or an error if the key "
        "doesn't exist. Always check for missing keys and initialize defaults "
        "when a key is not found."
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

# ── workflow_wait_for_user ────────────────────────────────────────────────

WORKFLOW_WAIT_FOR_USER = {
    "name": "workflow_wait_for_user",
    "description": (
        "Pause the current workflow and wait for a human to provide input "
        "before continuing. Use this when:\n"
        "- You need approval for an action or decision\n"
        "- You are uncertain and need human guidance\n"
        "- A task requires information only the user can provide\n"
        "- You want the user to confirm before proceeding with something risky\n\n"
        "The workflow will be paused and listed as pending. The human can "
        "respond with workflow_submit_response or via the /workflows command. "
        "On the NEXT cron run, use workflow_load_state to check for the "
        "response. ALWAYS include enough context so the human understands "
        "what they're being asked about.\n\n"
        "IMPORTANT: After calling this, the current cron run should END. "
        "The workflow will resume on a future run once the human responds."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": (
                    "A unique ID for this workflow pause point. Use a "
                    "descriptive name like 'assignment_approval_20260526' or "
                    "'review_assignment_bob'. This ID is used to match the "
                    "human's response back to this specific question."
                ),
            },
            "question": {
                "type": "string",
                "description": (
                    "The specific question or decision the human needs to "
                    "respond to. Be clear and actionable. Example: 'Should I "
                    "assign this review to Bob? He has the least recent "
                    "assignment but was on PTO last week.'"
                ),
            },
            "cron_job_id": {
                "type": "string",
                "description": (
                    "Optional: The cron job ID this workflow belongs to. "
                    "Helps organize pending workflows by job. If the agent "
                    "knows the cron job ID, pass it here."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Additional context to present to the human reviewer. "
                    "Include relevant history, current state, options "
                    "considered, and the reasoning behind the question. "
                    "This is shown alongside the question. Can be a JSON "
                    "string for structured data."
                ),
            },
        },
        "required": ["workflow_id", "question"],
    },
}

# ── workflow_submit_response ──────────────────────────────────────────────

WORKFLOW_SUBMIT_RESPONSE = {
    "name": "workflow_submit_response",
    "description": (
        "Submit a human's response to a previously paused workflow, resuming "
        "it. This is typically called by the human via a slash command or "
        "chat, NOT by the agent during a cron run.\n\n"
        "When a response is submitted, the workflow is marked as 'resolved' "
        "and the agent will see the response on its next cron execution "
        "when it calls workflow_load_state or the pre_llm_call hook injects it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {
                "type": "string",
                "description": "The workflow ID to respond to (same as used in workflow_wait_for_user).",
            },
            "response": {
                "type": "string",
                "description": "The human's answer, decision, or instruction.",
            },
        },
        "required": ["workflow_id", "response"],
    },
}

# ── workflow_list_pending ─────────────────────────────────────────────────

WORKFLOW_LIST_PENDING = {
    "name": "workflow_list_pending",
    "description": (
        "List all workflows that are currently waiting for human input. "
        "Use this at the START of a cron job to check if any previous "
        "workflows have been paused and are awaiting responses. Also useful "
        "for humans to review pending approvals via /workflows.\n\n"
        "Returns the list of pending workflows with their questions, context, "
        "and creation timestamps. Filter by cron_job_id if you only care "
        "about a specific job's pending workflows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cron_job_id": {
                "type": "string",
                "description": (
                    "Optional: Filter to only show pending workflows for "
                    "a specific cron job. Omit to see all pending workflows."
                ),
            },
        },
        "required": [],
    },
}

# ── All schemas ───────────────────────────────────────────────────────────

ALL_SCHEMAS = [
    WORKFLOW_SAVE_STATE,
    WORKFLOW_LOAD_STATE,
    WORKFLOW_WAIT_FOR_USER,
    WORKFLOW_SUBMIT_RESPONSE,
    WORKFLOW_LIST_PENDING,
]
