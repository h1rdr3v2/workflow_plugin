# Workflow Engine Plugin for Hermes

A first-class **workflow engine** that lets users and agents create scheduled, cron-like workflows with persistent per-workflow state and human-in-the-loop interactions.

## What It Does

- **Create workflows** — define a named job with a cron (or one-shot) schedule and an agent prompt, via chat or dashboard
- **Built-in scheduler** — a croniter-based polling loop manages all triggers; no reliance on Hermes cron
- **Persistent state** — each workflow has its own namespaced key-value store that survives across runs
- **Human-in-the-loop** — agents can pause mid-run and ask a human for input; the answer resumes the workflow immediately
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
User: Create a workflow that rotates PR review assignments every weekday at 9am
      among Alice, Bob, and Carol. If you're unsure about an assignment, ask me.

Agent: [calls workflow_create with appropriate params]

# Or via the dashboard: open Hermes → Workflows tab → "+ New Workflow"
```

## Architecture

```
~/.hermes/plugins/workflow/
├── plugin.yaml              # Manifest: 14 tools, 1 hook
├── __init__.py              # register(ctx) — wires tools, hooks, scheduler, skill, agent invocation
├── db.py                    # JSON-file store (~/.hermes/workflow_engine/)
├── scheduler.py             # croniter-based polling scheduler + gateway delivery
├── executor.py              # Builds the prompt and runs a single workflow tick (in-process AIAgent)
├── schemas.py               # LLM-visible tool definitions
├── tools.py                 # Tool handler implementations
├── skills/
│   └── workflow-agent/
│       └── SKILL.md          # Teaches agents the workflow pattern
├── dashboard/
│   ├── manifest.json         # Sidebar tab config
│   ├── plugin_api.py         # FastAPI backend routes
│   └── dist/
│       └── index.js          # React UI (hand-written IIFE, no build step)
└── README.md
```

## Tools

| Tool                       | Purpose                                                          |
| -------------------------- | ---------------------------------------------------------------- |
| `workflow_create`          | Create a new scheduled (or one-shot) workflow                    |
| `workflow_update`          | Modify a workflow (schedule, prompt, origin, deliver, notify, …) |
| `workflow_delete`          | Delete a workflow and all its state                              |
| `workflow_list`            | List all workflows with status                                   |
| `workflow_get`             | Get full details of a workflow + state keys + pending            |
| `workflow_enable`          | Re-enable a paused workflow                                      |
| `workflow_disable`         | Pause a workflow (stops it firing until re-enabled)              |
| `workflow_save_state`      | Persist a key-value pair for the current workflow                |
| `workflow_load_state`      | Retrieve a saved state value                                     |
| `workflow_delete_state`    | Remove a state key                                               |
| `workflow_wait_for_user`   | Pause and ask a human a question (delivers it to the chat)       |
| `workflow_submit_response` | Record a human's answer to a pending question                    |
| `workflow_list_pending`    | List all pending human-input requests                            |
| `workflow_send_message`    | Send a one-way status update/result to the workflow's chat       |

## Hooks

| Hook              | Purpose                        |
| ----------------- | ------------------------------ |
| `plugin_shutdown` | Gracefully stops the scheduler |

> Workflow state, resolved responses, and pending questions are injected directly into the agent's prompt by the executor at run time — there is no `pre_llm_call` hook (Hermes does not pass a `workflow_id` to that hook, so it could not identify the running workflow).

## Human Interface

- **`/workflows`** — list all workflows and pending actions
- **`/workflows respond <action_id> <answer>`** — answer a pending question (resumes the workflow immediately)
- **`/workflows dismiss <action_id>`** — dismiss without responding
- **Dashboard tab** — full CRUD, state inspection, pending action management (including respond/dismiss)

> When a workflow pauses, its question is delivered to the workflow's chat (if it has an `origin`) so you're notified — but answers come back through `/workflows respond` or the dashboard, not by replying in the chat.

## Delivery & Notification

Each workflow has two delivery-related settings:

- **`deliver`** — where output and messages go: `local` (no chat delivery), a platform (`discord`, `telegram`, `slack`, `email`), or `origin` (the chat it was created in).
- **`notify`** — how much to say when a run *succeeds*:
  - `summary` (default) — deliver the agent's final summary
  - `minimal` — deliver only a static `✅ Workflow <name> completed` line (best for side-effect jobs)
  - `silent` — deliver nothing on success (errors are always reported)

## Execution Flow

```
Scheduler fires a tick (cron, one-shot timer, or immediate resume)
  → Executor loads workflow + state + resolved responses
    → Builds the system prompt (state/responses/pending injected here)
      → Runs an in-process AIAgent with the full Hermes tool surface
        → Run recorded as: success | paused (awaiting human) | error
          → Output delivered per `deliver` + `notify`
```

- **Overlap protection** — a workflow with an unresolved pending action won't trigger again until it's resolved.
- **Immediate resume** — a human response (`/workflows respond`, dashboard, or `workflow_submit_response`) resumes the workflow at once. Resumes always run on a background thread, never on the gateway/web event loop.
- **One-shot** — workflows created with `trigger_at`/`trigger_in` fire once and auto-disable.

## Storage

State lives under `~/.hermes/workflow_engine/` as plain JSON (atomic writes, thread-safe):

| Path                       | Purpose                                                     |
| -------------------------- | ----------------------------------------------------------- |
| `workflows.json`           | Workflow definitions                                        |
| `pending.json`             | Human-in-the-loop requests (question, status, response, …)  |
| `states/{workflow_id}.json`| Per-workflow key-value state                                |

All state is scoped by `workflow_id` — each workflow has its own logical namespace.

## Requirements

- Hermes Agent (plugin-capable version)
- Python 3.10+
- `croniter` (core Hermes dependency, always available)

## Design Decisions

- **Built-in scheduler** via croniter — workflows don't depend on Hermes cron jobs
- **JSON file store** — simple, portable, inspectable, no external dependencies or migrations
- **Overlap protection** — a workflow with an unresolved pending action won't fire again until resolved
- **Off-thread resumes** — resuming a workflow never blocks the gateway or web event loop
- **State auto-injected** — agents don't need to manually load every key; the executor injects a state summary into the system prompt each run
