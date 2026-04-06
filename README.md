# cal-trigger

cal-trigger is a lightweight Python daemon that polls a Google Calendar iCal feed and triggers AI agent actions when it detects matching events. Calendar is the most universal personal planning surface (2B+ users) — cal-trigger bridges the gap between visual, temporal planning and autonomous agent dispatch, so you can schedule agent work the same way you schedule meetings: just create an event, prefix the title with `[agent]`, and cal-trigger handles the rest.

---

## Quickstart

**Step 1 — Get your Google Calendar iCal URL**

Open Google Calendar > Settings > select your calendar > scroll to "Integrate calendar" > copy the "Secret address in iCal format" (it ends in `/basic.ics`).

**Step 2 — Edit the config**

```bash
cp config.example.yml config.yml
```

Open `config.yml` and set `ical_url` to the URL you copied. Set `trigger.command` to whatever you want to run when an event fires.

**Step 3 — Run the daemon**

```bash
pip install -r requirements.txt
python cal_trigger.py
```

The daemon will log its activity to stdout. Create a Google Calendar event whose title starts with `[agent]` and scheduled to start within the next 5 minutes — you will see it triggered in the next poll cycle.

---

## Event Convention

Events are matched by a configurable prefix on the event title (default: `[agent]`). The prefix is stripped before the task is dispatched. Anything in the event description is passed through as the task description.

```
Calendar event title:       [agent] Summarize Q1 sales report
Calendar event description: Focus on APAC region. Output to Slack #reports.

Dispatched task dict:
{
  "title":       "Summarize Q1 sales report",
  "description": "Focus on APAC region. Output to Slack #reports.",
  "start":       "2026-04-08T09:00:00+00:00",
  "end":         "2026-04-08T09:30:00+00:00",
  "uid":         "abc123@google.com"
}
```

More examples:

```
[agent] Draft weekly newsletter
[agent] Run competitor pricing analysis
[agent] Backup database and send summary
```

Each occurrence of an event is dispatched at most once. Dispatch state persists in `dispatched.json` and survives daemon restarts.

---

## Adapter Configuration

The `trigger` block in `config.yml` selects which adapter fires when an event is detected. Four adapters are included.

### Shell (universal fallback)

Runs any shell command. Supports `{title}`, `{description}`, `{start}`, `{end}`, and `{uid}` placeholders. Returns success if the command exits with code 0.

```yaml
trigger:
  type: shell
  command: "echo '{title}'"
```

Any CLI-invokable agent works here. For example, Claude Code:

```yaml
trigger:
  type: shell
  command: "claude -p '{title}: {description}'"
```

### OpenClaw (first-class integration)

Invokes the `openclaw agent` CLI, which dispatches a single agent turn over the OpenClaw Gateway. Handles argument escaping and optional reply delivery routing.

```yaml
trigger:
  type: openclaw
  agent: "my-research-agent"   # agent name from your openclaw config
  local: false                  # true = embedded execution, skips Gateway round-trip
  deliver: true                 # route the agent's reply to a messaging channel
  reply_channel: "slack"        # slack | telegram | whatsapp | signal | ...
  reply_to: "C0123456789"       # channel ID, user ID, phone number, etc.
  timeout: 30                   # seconds before giving up (default: 30)
```

Minimal config:

```yaml
trigger:
  type: openclaw
  agent: "default"
```

### HTTP (covers Orbital and any API-based agent)

POSTs the task dict as JSON to any HTTP endpoint. Works with Orbital, LangGraph Platform, CrewAI Enterprise, AutoGen-wrapped FastAPI services, and any other agent with an HTTP interface.

```yaml
trigger:
  type: http
  url: "http://localhost:8000/api/v1/trigger"
  method: POST                  # POST (default) or PUT
  headers:
    Authorization: "Bearer your-token-here"
    X-Custom-Header: "value"
  timeout: 30                   # seconds (default: 30)
```

The full task dict (`title`, `description`, `start`, `end`, `uid`) is sent as the JSON body. Returns success on any 2xx response.

Examples for specific platforms:

```yaml
# LangGraph Platform
trigger:
  type: http
  url: "http://localhost:2024/runs/stream"
  headers:
    Content-Type: "application/json"

# CrewAI Enterprise
trigger:
  type: http
  url: "https://your-crew.crewai.com/kickoff"
  headers:
    Authorization: "Bearer your-crewai-token"
```

### Python (covers CrewAI, LangGraph, AutoGen, etc.)

Imports a Python module and calls a function with the task dict. Best for in-process frameworks where you want zero network overhead.

```yaml
trigger:
  type: python
  module: "my_agent_adapter"    # importable module name (must be on sys.path)
  function: "handle_task"       # function to call (default: "handle_task")
```

The referenced function must accept a single `task: dict` argument:

```python
# my_agent_adapter.py
def handle_task(task: dict) -> bool:
    from my_crew import build_crew
    crew = build_crew()
    result = crew.kickoff(inputs=task)
    return bool(result)
```

Returning `False` is treated as a failure — the event will be retried on the next poll cycle. Returning `True`, `None`, or any truthy value is treated as success.

---

## Add Your Own Adapter

1. Create a file in `adapters/`:

```python
# adapters/my_adapter.py
import logging

logger = logging.getLogger(__name__)

def trigger(task: dict, config: dict) -> bool:
    """
    Dispatch a task. Return True on success, False on failure.
    Failed events are retried on the next poll cycle.
    """
    logger.info("my_adapter: dispatching '%s'", task["title"])
    # ... your dispatch logic here ...
    return True
```

2. Register it in `adapters/__init__.py`:

```python
elif adapter_type == "my_adapter":
    from .my_adapter import trigger
```

3. Use it in `config.yml`:

```yaml
trigger:
  type: my_adapter
  # any additional keys are passed to trigger() as config
```

The `config` argument to `trigger()` is the entire `trigger` block from `config.yml`, minus the `type` key.

---

## Limitations of v1

- **Google Calendar only.** Any iCal feed works in principle, but only Google Calendar's iCal export has been tested.
- **No recurring event support.** Each occurrence of a recurring event has the same UID. The deduplication key is `uid::YYYY-MM-DD`, so the first occurrence on a given date triggers correctly, but if the same event recurs the daemon will re-trigger it on the next date. This is intentional behavior but may surprise users who expect "trigger once ever".
- **Lookahead window only.** Events are only detected if their start time falls within `(now, now + lookahead_minutes]`. Past events are never triggered. This means if the daemon is offline when an event fires, that event will not be caught when the daemon restarts.
- **Single calendar.** Only one iCal URL is supported per daemon instance. Run multiple instances with different config files to watch multiple calendars.
- **No async execution.** Adapter calls are synchronous and block the poll loop. A slow adapter (e.g., a long-running LLM job) will delay the next poll. Use the `shell` adapter with a background-launch command (`my-agent &`) if you need non-blocking dispatch.
- **OpenClaw HTTP webhook regression.** As of OpenClaw v2026.3.13, `POST /hooks/agent` returns 404 (issue #48212). The `openclaw` CLI adapter is unaffected. The `http` adapter can target this endpoint once the regression is fixed.
- **Windows encoding.** On Chinese-locale Windows, logging may raise `UnicodeEncodeError` for non-ASCII characters. Set `PYTHONIOENCODING=utf-8` or run `chcp 65001` in your terminal to work around this.

---

## Framework Compatibility

See [RESEARCH.md](RESEARCH.md) for detailed notes on OpenClaw, Orbital, CrewAI, LangGraph, AutoGen, and Claude Code integration patterns, including latency breakdowns and known issues.
