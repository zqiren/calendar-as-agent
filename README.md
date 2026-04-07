# cal-trigger

**Schedule AI agents from your calendar. Just create an event.**

You already plan your day in Google Calendar. Now your AI agents can read it too.

`cal-trigger` watches your calendar for events prefixed with `[agent]` and automatically dispatches them to any AI agent framework -- OpenClaw, CrewAI, LangGraph, Claude Code, or anything with a CLI, HTTP API, or Python interface. No new UI. No dashboards. Just your calendar.

```
  Google Calendar                    cal-trigger                     Your Agent
 +---------------------+           +-----------+           +----------------------+
 | [agent] Summarize   |  ------>  |  poll +   |  ------>  | Runs the task,       |
 |   Q1 sales report   |  (iCal)  |  match +  |  (shell/  | returns results      |
 |   9:00 - 9:30 AM    |          |  dispatch |   http/   |                      |
 +---------------------+          +-----------+   python)  +----------------------+
```

---

## The Problem

AI agents are powerful, but triggering them is awkward. You either type into a CLI, wire up a cron job, or build a custom integration. None of these fit how most people actually plan their work: **on a calendar**.

Calendar is the most universal planning surface on earth (2B+ Google Calendar users alone). It's visual, temporal, shareable, and already part of every knowledge worker's daily routine. But today, no open-source tool bridges calendar events to agent dispatch.

**cal-trigger fills that gap.** One lightweight daemon. Any agent framework. Zero lock-in.

---

## How It Works

1. You create a Google Calendar event with the `[agent]` prefix
2. `cal-trigger` polls your calendar's iCal feed every 60 seconds
3. When it detects a matching event within the lookahead window (default: 5 minutes before start), it fires your configured trigger
4. The event is marked as dispatched so it never fires twice
5. If the trigger fails, it retries on the next poll cycle

```
Calendar event:         [agent] Research competitor pricing
Event description:      Compare Cursor, Windsurf, Bolt. Output to #research Slack.
Event time:             Tuesday 2:00 PM - 3:00 PM

What your agent receives:
{
  "title":       "Research competitor pricing",
  "description": "Compare Cursor, Windsurf, Bolt. Output to #research Slack.",
  "start":       "2026-04-08T14:00:00+00:00",
  "end":         "2026-04-08T15:00:00+00:00",
  "uid":         "abc123@google.com"
}
```

The `[agent]` prefix is stripped. The description, start/end times, and event UID are all passed through. Regular calendar events (without the prefix) are completely ignored.

---

## Quickstart (5 minutes)

**Step 1 -- Get your iCal URL**

Google Calendar > Settings > select your calendar > "Integrate calendar" > copy "Secret address in iCal format" (ends in `/basic.ics`).

**Step 2 -- Configure**

```bash
git clone https://github.com/zqiren/calendar-as-agent.git
cd calendar-as-agent
cp config.example.yml config.yml
```

Edit `config.yml` -- set your `ical_url` and choose a trigger:

```yaml
ical_url: "https://calendar.google.com/calendar/ical/YOUR_ID/basic.ics"
trigger:
  type: shell
  command: "echo 'Task: {title} | {description}'"
```

**Step 3 -- Run**

```bash
pip install -r requirements.txt
python cal_trigger.py
```

Now create a calendar event titled `[agent] Hello world` starting in the next 5 minutes. You'll see it fire on the next poll cycle.

---

## Use Cases

- **Daily standup prep** -- Schedule `[agent] Summarize yesterday's PRs and Slack threads` every morning at 8:45 AM
- **Recurring research** -- Weekly `[agent] Competitor pricing analysis` that runs every Monday
- **Meeting prep** -- `[agent] Brief me on attendees and agenda` 15 minutes before important meetings
- **Automated reports** -- `[agent] Generate weekly metrics dashboard` every Friday at 4 PM
- **Personal automation** -- `[agent] Backup my notes and email summary` at end of day

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
