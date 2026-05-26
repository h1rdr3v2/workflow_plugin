# Workflow Engine Plugin for Hermes

A first-class **workflow engine** that lets users and agents create scheduled cron-like workflows with persistent per-workflow state and human-in-the-loop interactions.

## What It Does

- **Create workflows** — define a named job with a cron schedule and agent prompt, via chat or dashboard
- **Built-in scheduler** — APScheduler manages all cron triggers, no reliance on Hermes cron
- **Persistent state** — each workflow has its own namespaced key-value store that survives across runs
- **Human-in-the-loop** — agents can pause mid-run and ask a human for input; the response is injected on the next tick
- **Dashboard** — full UI for creating, editing, enabling/disabling, and monitoring workflows

## Quick Start

```bash
# 1. Copy into your Hermes plugins directory
cp -r workflow ~/.hermes/plugins/

# 2. Enable the plugin
hermes plugins enable workflow

# 3. Verify it loaded
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

### Creating Your First Workflow (via agent chat)

```
User: Create a workflow that rotates PR review assignments every weekday at 9am among Alice, Bob, and Carol. If you're unsure about an assignment, ask me.

Agent: [calls workflow_create with appropriate params]

# Or via the dashboard: open Hermes → Workflows tab → "+ New Workflow"
```

## Architecture

```
~/.hermes/plugins/workflow/
├── plugin.yaml              # Manifest: 11 tools, 2 hooks
├── __init__.py              # register(ctx) — wires tools, hooks, scheduler, skill
├── models.py                # Workflow, WorkflowRun, PendingAction dataclasses
├── db.py                    # SQLite layer (workflow_engine.db) — 3 tables
├── scheduler.py             # APScheduler cron loop
├── executor.py              # Runs a single workflow tick
├── schemas.py               # 11 LLM-visible tool definitions
├── tools.py                 # 11 tool handler implementations
├── skills/
│   └── workflow-agent/
│       └── SKILL.md          # Teaches agents the workflow pattern
├── dashboard/
│   ├── manifest.json         # Sidebar tab config
│   ├── plugin_api.py         # FastAPI backend routes
│   └── dist/
│       └── index.js          # React UI (IIFE, no build step)
└── README.md
```

## Tools

| #   | Tool                       | Purpose                                              |
| --- | -------------------------- | ---------------------------------------------------- |
| 1   | `workflow_create`          | Create a new scheduled workflow                      |
| 2   | `workflow_update`          | Modify a workflow (schedule, prompt, enable/disable) |
| 3   | `workflow_delete`          | Delete a workflow and all its state                  |
| 4   | `workflow_list`            | List all workflows with status                       |
| 5   | `workflow_get`             | Get full details of a workflow + state + pending     |
| 6   | `workflow_save_state`      | Persist a key-value pair for the current workflow    |
| 7   | `workflow_load_state`      | Retrieve a saved state value                         |
| 8   | `workflow_delete_state`    | Remove a state key                                   |
| 9   | `workflow_wait_for_user`   | Pause and ask a human a question                     |
| 10  | `workflow_submit_response` | Human answers a pending question                     |
| 11  | `workflow_list_pending`    | List all pending human-input requests                |

## Hooks

| Hook              | Purpose                                                                                         |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| `pre_llm_call`    | Injects workflow state + pending/resolved responses into agent context at the start of each run |
| `plugin_shutdown` | Gracefully stops the APScheduler                                                                |

## Human Interface

- **`/workflows`** — list all workflows and pending actions
- **`/workflows respond <action_id> <answer>`** — respond to a pending question
- **`/workflows dismiss <action_id>`** — dismiss without responding
- **Dashboard tab** — full CRUD, state inspection, pending action management

## Execution Flow

```
Scheduler triggers cron tick
  → Load workflow definition + saved state + resolved responses
    → Build enriched agent prompt
      → Agent runs, calling tools (load_state, save_state, wait_for_user, etc.)
        → Run recorded as: success | paused (awaiting human) | error
```

- **Overlap protection**: if a workflow has an unresolved pending action, the scheduler skips the tick (workflow is effectively "paused")
- **Manual trigger**: you can run any workflow ad-hoc via the dashboard or API without waiting for its cron schedule

## Database

State is stored in `~/.hermes/workflow_engine.db` (SQLite, WAL mode):

| Table             | Purpose                                                                  |
| ----------------- | ------------------------------------------------------------------------ |
| `workflows`       | Workflow definitions (id, name, cron, prompt, enabled)                   |
| `workflow_state`  | Per-workflow key-value store (workflow_id, key, value)                   |
| `pending_actions` | Human-in-the-loop requests (id, workflow_id, question, status, response) |

All state is scoped by `workflow_id` — each workflow has its own logical namespace.

## Requirements

- Hermes Agent (plugin-capable version)
- Python 3.10+
- `apscheduler` (`pip install apscheduler`)
- `sqlite3` (bundled with Python)

## Design Decisions

- **Built-in scheduler** via APScheduler — workflows don't depend on Hermes cron jobs
- **Single SQLite DB** with per-workflow namespacing — simple, portable, no external dependencies
- **Overlap protection** — a workflow with an unresolved pending action won't trigger again until resolved
- **Thread-safe** — same BEGIN IMMEDIATE + jitter retry pattern as Hermes's own SessionDB
- **JSON state values** — flexible enough for any workflow shape without schema migrations
- **All state auto-injected** — agents don't need to manually call `load_state` for every key; the `pre_llm_call` hook injects state context automatically
