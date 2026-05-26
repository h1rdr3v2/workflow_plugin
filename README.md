# Workflow Plugin for Hermes Agent

A persistent workflow/memory engine that adds **stateful execution**, **human-in-the-loop interactions**, and **cross-run memory** to Hermes cron agents.

## The Problem

Hermes cron jobs are **stateless**. Every cron execution starts as a fresh session — the agent cannot remember previous runs, ongoing workflows, assignments, pending approvals, or user interactions.

## The Solution

A lightweight plugin that sits alongside Hermes (no core modifications needed) and provides:

- **Persistent state** — agents save/load data across cron runs via SQLite
- **Human pause/resume** — agents pause for approval, humans respond via `/workflows`
- **Workflow continuation** — paused workflows resume with full context on the next cron tick
- **Task rotation tracking** — agents remember assignment history and rotate fairly

## Quick Start

```bash
# 1. Copy into your Hermes plugins directory
cp -r workflow ~/.hermes/plugins/

# 2. Enable the plugin
hermes plugins enable workflow

# 3. Verify it loaded
HERMES_PLUGINS_DEBUG=1 hermes plugins list

# 4. Give your agent a stateful cron job
hermes cron create \
  --name "Task Rotation" \
  --schedule "0 9 * * *" \
  --prompt "You are a workflow agent. Use workflow_load_state to check previous assignments, rotate tasks fairly among Alice/Bob/Carol, then use workflow_save_state to persist the updated rotation. If unsure about an assignment, use workflow_wait_for_user to ask me."
```

## Architecture

```
~/.hermes/plugins/workflow/
├── plugin.yaml           # Manifest — declares tools, hooks
├── __init__.py           # register(ctx) — wires everything
├── db.py                 # SQLite layer (workflow_engine.db)
├── schemas.py            # 5 LLM-visible tool definitions
├── tools.py              # Tool handler implementations
├── skills/
│   └── workflow-agent/
│       └── SKILL.md      # Teaches agents the workflow pattern
└── dashboard/            # Dashboard UI plugin
    ├── manifest.json     # Sidebar tab config
    ├── plugin_api.py     # FastAPI backend routes
    └── dist/
        └── index.js      # React UI (IIFE, no build step)
```

## Tools

| Tool                                              | Purpose                          |
| ------------------------------------------------- | -------------------------------- |
| `workflow_save_state(key, value)`                 | Persist data across cron runs    |
| `workflow_load_state(key)`                        | Retrieve saved data              |
| `workflow_wait_for_user(workflow_id, question)`   | Pause workflow, wait for human   |
| `workflow_submit_response(workflow_id, response)` | Human answers a pending workflow |
| `workflow_list_pending()`                         | List workflows awaiting input    |

## Human Interface

- `/workflows` — list all pending workflows
- `/workflows respond <id> <answer>` — respond to a pending workflow
- `/workflows dismiss <id>` — dismiss a pending workflow without responding

## How It Works

1. **On each cron run**, the `pre_llm_call` hook injects saved state and pending human responses into the agent's context
2. **The agent** loads previous state, makes decisions, saves new state
3. **If stuck**, the agent calls `workflow_wait_for_user()` and the job ends
4. **A human** reviews pending workflows via `/workflows` and submits a response
5. **On the next cron run**, the agent sees the human's response and continues

## Database

State is stored in `~/.hermes/workflow_engine.db` (SQLite, WAL mode):

- `workflow_state` — JSON key-value store
- `pending_actions` — tracks workflows awaiting human input

## Requirements

- Hermes Agent (any version that supports plugins)
- Python 3.10+ with `sqlite3` (bundled with Python)

## Design Decisions

- **Separate database** from Hermes core `state.db` — no coupling, easy to evolve
- **JSON serialization** — flexible enough for any workflow shape without schema migrations
- **`pre_llm_call` hook** — follows Hermes's documented context injection pattern
- **No core modifications** — uses only public `ctx.register_*` APIs
- **Thread-safe** — uses same BEGIN IMMEDIATE + jitter retry pattern as Hermes's own SessionDB
