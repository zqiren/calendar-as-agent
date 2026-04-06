# Cal-Trigger: Calendar → Agent Dispatch Daemon

## Task Spec

**Build a lightweight Python daemon that polls a Google Calendar iCal feed and triggers an AI agent when it detects matching events. Primary integration target is OpenClaw. Ships as open-source community tool for Orbital's launch.**

---

## Required Inputs (to be provided)

- **iCal URL**: `https://calendar.google.com/calendar/ical/zqzqzqr0%40gmail.com/private-ce2fc4e318a8a95c6163e981af528ce3/basic.ics`
- **Orbital repo link**: `https://github.com/zqiren/Orbital`
- **Orbital trigger mechanism docs/notes**: `{{ORBITAL_TRIGGER_NOTES}}`
- **OpenClaw agent ID for testing** (if available): `{{OPENCLAW_AGENT_ID}}`

---

## Part 0: Research Phase (do this first)

Before writing any code, research and document the following trigger mechanisms. This research will inform the trigger adapter design.

### 0a. OpenClaw Message Injection

OpenClaw supports seven trigger types (message, cron, CLI, filesystem, webhook, inter-agent, startup). For this project, the relevant paths are:

**CLI (primary, confirmed working):**

```bash
openclaw agent --agent <id> --message "<task>" --local
openclaw agent --agent <id> --message "<task>" --deliver --reply-channel <channel> --reply-to "<target>"
```

The `openclaw agent` command runs a single agent turn via the Gateway, with `--local` fallback for embedded execution. The `--deliver` flag routes the agent's response to a specified channel (Slack, Telegram, WhatsApp, etc.).

**WebSocket Gateway:** OpenClaw runs a WebSocket control plane at `ws://127.0.0.1:18789`. Direct WS message injection is possible but underdocumented for external callers.

> **Research task:** Check if there's a simple WS message format for triggering an agent turn. This would avoid the 300-700ms CLI process fork overhead per trigger.

**Webhook surface:** OpenClaw docs reference a webhook surface for external triggers.

> **Research task:** Confirm whether `openclaw gateway` exposes an HTTP webhook endpoint that accepts inbound task messages. Known issue: GitHub #12874 showed `POST /api/sessions/spawn` returns 405 — this may not be viable yet.

**Cron trigger:** OpenClaw has native cron scheduling (`openclaw cron add`).

> **Research task:** Evaluate whether it makes more sense to register the calendar poll loop as an OpenClaw cron job rather than a standalone daemon. This would eliminate a separate process but couples the tool to OpenClaw.

**Document findings in a `RESEARCH.md` file** with working/non-working paths, code examples, and latency observations.

### 0b. Orbital Trigger Mechanism

Orbital is our agent platform. Repo: `{{ORBITAL_REPO_URL}}`

Additional context: `{{ORBITAL_TRIGGER_NOTES}}`

> **Research task:** Review the Orbital repo and any available docs to determine how Orbital agents accept external task triggers — HTTP API, CLI, message queue, or other. Document the interface in `RESEARCH.md`. If no public docs exist, design the trigger adapter interface such that adding an Orbital adapter is trivial.

### 0c. Generic Agent Trigger Patterns

Survey how these frameworks accept external triggers, and document in `RESEARCH.md`:

- **CrewAI:** `crew.kickoff(inputs={...})` Python API
- **LangGraph:** typically HTTP server or Python function call
- **AutoGen / Microsoft Agent Framework:** async message passing
- **Claude Code:** `claude -p "message"` for one-shot prompts (coding-focused, not general agent dispatch)

This research informs whether the adapter interface is sufficient for broad ecosystem coverage.

---

## Part 1: Build the Daemon

A Python script (`cal_trigger.py`) that:

1. Reads a config file (`config.yml`) specifying: iCal URL, poll interval, trigger adapter config, and event matching convention
2. Polls the iCal URL on the configured interval
3. Detects events matching the convention (default: events with title prefix `[agent]`)
4. On detecting a new or unprocessed matching event, invokes the configured trigger adapter with task metadata
5. Tracks which events have already been dispatched (simple local JSON file) so events are not triggered twice
6. Logs activity to stdout

### Trigger Adapter Pattern

Instead of a single shell command template, use a simple adapter pattern supporting multiple trigger methods:

```yaml
# config.yml

ical_url: "https://calendar.google.com/calendar/ical/xxx/basic.ics"
poll_interval: 60
event_prefix: "[agent]"

trigger:
  type: "shell"  # or "openclaw" or "http" or "python"

  # --- shell adapter (universal fallback) ---
  # type: shell
  # command: "echo '{task}'"

  # --- openclaw adapter (recommended for OpenClaw users) ---
  # type: openclaw
  # agent: "default"
  # local: true
  # deliver: false
  # reply_channel: "telegram"
  # reply_to: "@me"

  # --- http adapter (covers Orbital and any API-based agent) ---
  # type: http
  # url: "http://localhost:8000/trigger"
  # method: POST
  # headers:
  #   Authorization: "Bearer xxx"

  # --- python adapter (covers CrewAI, LangGraph, etc.) ---
  # type: python
  # module: "my_adapter"
  # function: "handle_task"
```

### Adapters to Implement

1. **`shell`** — Executes a command template with `{title}`, `{description}`, `{start}`, `{end}`, `{uid}` substitution. Use `subprocess.run` with argument list (NOT `shell=True`) to avoid injection. Universal fallback.

2. **`openclaw`** — Calls `openclaw agent --agent <id> --message <task>` with proper argument escaping. Supports `--local`, `--deliver`, `--reply-channel`, `--reply-to` from config. First-class integration.

3. **`http`** — Sends POST/PUT with task metadata as JSON body. Supports custom headers. Covers Orbital and any agent behind an API.

4. **`python`** — Imports a user-specified module and calls a function with a task dict. Covers CrewAI, LangGraph, and any Python-based framework.

### Task Metadata

Template variables available across all adapters:

- `{title}` — event title with prefix stripped
- `{description}` — event description body
- `{start}` — event start time ISO format
- `{end}` — event end time ISO format
- `{uid}` — event unique ID

For structured adapters (http, python, openclaw), pass a task dict:

```python
{
    "title": "Research competitor pricing",
    "description": "Compare Cursor, Windsurf, Bolt...",
    "start": "2026-04-08T14:00:00Z",
    "end": "2026-04-08T15:00:00Z",
    "uid": "abc123@google.com"
}
```

### State Tracking

- Maintain a `dispatched.json` file storing UIDs of already-triggered events
- Only trigger events whose UID is not in dispatched AND whose start time is within a configurable lookahead window (default: events starting within next 5 minutes)
- After successful trigger, write UID to `dispatched.json`
- On trigger failure (non-zero exit, HTTP error, exception): log error, do NOT add to dispatched so it retries next cycle

### Edge Cases

- **Recurring events:** each occurrence = separate trigger (UID + occurrence date as compound key)
- **Modified events:** if already-dispatched, do NOT re-trigger (v1 simplicity)
- **Deleted events:** ignore, no action needed
- **Network errors on poll:** log warning, retry next cycle
- **Malformed iCal:** log and skip bad events, don't crash
- **Command injection:** never use `shell=True`, always pass args as list

---

## Part 2: Smoke Test Against Real Google Calendar

### Prerequisites

A Google Calendar with iCal secret URL: `{{ICAL_URL}}`

### Test Sequence

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Create `config.yml` with iCal URL, `trigger.type: shell`, command: `echo` | Config loads without error |
| 2 | Create Google Calendar event: title `[agent] Test task from calendar`, description `This is a smoke test. Confirm task received.`, start within next 5 min | Event visible in calendar |
| 3 | Run `cal_trigger.py` | Daemon starts, begins polling |
| 4 | Wait for poll cycle | Daemon detects event, prints parsed task metadata via echo |
| 5 | Check `dispatched.json` | Event UID is recorded |
| 6 | Wait for next poll cycle | Same event is NOT re-triggered |
| 7 | Create second event `[agent] Second test` | Triggers without daemon restart |
| 8 | Create non-agent event (no `[agent]` prefix) | Event is ignored |
| 9 | Switch to `trigger.type: openclaw` (if installed), create new agent event | `openclaw agent` invoked with correct args |
| 10 | Switch to `trigger.type: http`, run local HTTP server, create agent event | POST received with correct JSON payload |
| 11 | Configure trigger to failing command (`exit 1`), create agent event | Retries on next poll cycle, not added to dispatched |

**Document test results in `TEST_RESULTS.md`** with timestamps and pass/fail for each step.

---

## Part 3: Package for Open Source

### Project Structure

```
cal-trigger/
  cal_trigger.py          # single-file daemon (<250 lines)
  adapters/
    __init__.py
    shell.py              # shell command adapter
    openclaw.py           # openclaw CLI adapter
    http.py               # HTTP POST adapter
    python_adapter.py     # Python function adapter
  config.example.yml      # all adapter options commented
  RESEARCH.md             # findings from Part 0
  TEST_RESULTS.md         # smoke test results from Part 2
  README.md               # setup + usage + quickstart
  requirements.txt        # icalendar, pyyaml, requests
  LICENSE                 # MIT
  .gitignore              # dispatched.json, venv, __pycache__
```

### README Must Include

- One-paragraph description: what it does and why
- 3-step quickstart: get iCal URL → edit config → run script
- Adapter configuration examples for each type (OpenClaw, HTTP, shell, Python)
- Event convention explanation with visual examples
- "Add your own adapter" guide — implement `def trigger(task: dict) -> bool`
- Limitations of v1
- Link to `RESEARCH.md` for framework compatibility details

### Quality Bar

- Core daemon under 250 lines
- Each adapter under 50 lines
- Zero framework dependencies beyond `icalendar`, `pyyaml`, `requests`
- Git clone to running smoke test in under 5 minutes
- Clean enough to be Orbital's first open-source community artifact

---

## Strategic Context

**Why this exists:** Calendar is the most universal personal planning surface (2B+ users). Today, autonomous agents can only be triggered via chat, CLI, or cron. Cal-trigger adds a trigger modality that is visual, temporal, async, and already embedded in every knowledge worker's daily workflow.

**Why open source:** Small enough to ship fast, useful enough to get adoption, open enough to attract contributors, novel enough to generate organic attention. It demonstrates Orbital's philosophy: agents should meet users where they already are.

**Nobody owns this layer.** Google builds agent-calendar natively but only for Gemini. Reclaim/Motion treat calendar as scheduling optimizer, not agent dispatch. There is no open-source, framework-agnostic calendar-to-agent bridge.

**Positioning:** Community reputation builder for Orbital launch. Every user of this tool encounters the Orbital ecosystem. The OpenClaw adapter is first-class; the open adapter pattern invites the broader agent community.

---

## Timeline Estimate

| Phase | Effort |
|-------|--------|
| Part 0: Research | 0.5–1 day |
| Part 1: Daemon + adapters | 2–3 days |
| Part 2: Smoke test | 0.5 day |
| Part 3: Packaging + docs | 1–2 days |
| **Total** | **~1–2 weeks** |
