# cal-trigger: Trigger Mechanism Research

**Research date:** 2026-04-07  
**Purpose:** Inform the adapter design for cal-trigger, a framework-agnostic Google Calendar → AI agent bridge.

---

## Table of Contents

1. [0a. OpenClaw Message Injection](#0a-openclaw-message-injection)
   - [CLI (primary)](#cli-primary-confirmed-working)
   - [WebSocket Gateway](#websocket-gateway)
   - [HTTP Webhook Surface](#http-webhook-surface)
   - [Cron Trigger](#cron-trigger)
   - [OpenClaw Summary](#openclaw-summary-and-recommendation)
2. [0b. Orbital Trigger Mechanism](#0b-orbital-trigger-mechanism)
3. [0c. Generic Agent Trigger Patterns](#0c-generic-agent-trigger-patterns)
   - [CrewAI](#crewai)
   - [LangGraph](#langgraph)
   - [AutoGen / Microsoft Agent Framework](#autogen--microsoft-agent-framework)
   - [Claude Code](#claude-code)
4. [Adapter Coverage Matrix](#adapter-coverage-matrix)
5. [Sources](#sources)

---

## 0a. OpenClaw Message Injection

OpenClaw supports seven built-in trigger types: `message`, `cron`, `CLI`, `filesystem`, `webhook`, `inter-agent`, and `startup`. For external calendar-driven dispatch, three paths are viable: CLI, WebSocket, and HTTP webhook.

### CLI (primary, confirmed working)

The `openclaw agent` command is the canonical external-trigger path. It connects to the running Gateway over WebSocket and dispatches a single agent turn, then exits.

**Syntax:**

```bash
# Minimal — runs agent, output goes to stdout only
openclaw agent --agent <id> --message "<task>" --local

# With reply delivery (Slack, Telegram, WhatsApp, etc.)
openclaw agent --agent <id> --message "<task>" \
  --deliver \
  --reply-channel <channel> \
  --reply-to "<target>"
```

**Full flag reference:**

| Flag | Description |
|------|-------------|
| `--agent <id>` | Target a named agent from config |
| `-m / --message <text>` | Task text to inject (required) |
| `--local` | Force embedded agent execution; skips Gateway round-trip, still preloads plugin registry |
| `--deliver` | Route the agent's reply to a messaging channel |
| `--reply-channel <ch>` | Channel name: `slack`, `telegram`, `whatsapp`, `signal`, etc. |
| `--reply-to <target>` | Delivery target (user ID, channel ID, phone number, etc.) |
| `--reply-account <id>` | Delivery account override (for multi-account setups) |
| `--thinking <level>` | `off` / `minimal` / `low` / `medium` / `high` / `xhigh` |
| `--timeout <s>` | Override agent timeout (default: 600 s) |
| `--json` | Return JSON-formatted response to stdout |

**Execution path:**

1. CLI process starts (~300–700 ms Node.js startup + plugin registry preload)
2. Connects to Gateway at `ws://127.0.0.1:18789` (or embedded fallback with `--local`)
3. Sends task as a user message to the target session
4. Waits for agent completion, then exits

**Latency breakdown (from OpenClaw gateway internals):**

| Stage | Time |
|-------|------|
| Node.js startup + CLI parse | ~300–700 ms |
| WebSocket connect + auth | < 10 ms (local) |
| Session load from disk | < 50 ms |
| System prompt assembly | < 100 ms |
| First token from model | 200–500 ms (network) |

**Total overhead before LLM call: ~600 ms–1.4 s per invocation.** This is acceptable for calendar-driven tasks (which fire minutes before an event), but not for sub-second latency requirements.

**For cal-trigger:** Use the `openclaw` adapter. Construct args as a list (never `shell=True`) and call via `subprocess.run`. Example from the adapter:

```python
cmd = ["openclaw", "agent", "--agent", agent_id, "--message", task_text]
if config.get("local"):
    cmd.append("--local")
if config.get("deliver"):
    cmd += ["--deliver", "--reply-channel", config["reply_channel"],
            "--reply-to", config["reply_to"]]
subprocess.run(cmd, check=True)
```

---

### WebSocket Gateway

OpenClaw exposes a WebSocket control plane at `ws://127.0.0.1:18789` (loopback-only by default). Direct WS injection avoids the CLI process-fork overhead (~300–700 ms savings).

**Protocol:** Version 3. All frames are JSON text frames validated against TypeBox schemas. The connection requires a three-step handshake:

**Step 1 — Server challenge:**
```json
{
  "type": "event",
  "event": "connect.challenge",
  "payload": { "nonce": "…", "ts": 1737264000000 }
}
```

**Step 2 — Client connect (must sign the nonce):**
```json
{
  "type": "req",
  "id": "unique-id",
  "method": "connect",
  "params": {
    "minProtocol": 3,
    "maxProtocol": 3,
    "auth": { "token": "…" },
    "device": {
      "id": "device_fingerprint",
      "publicKey": "…",
      "signature": "…",
      "signedAt": 1737264000000,
      "nonce": "…"
    }
  }
}
```

**Step 3 — Server confirmation:**
```json
{ "type": "res", "id": "…", "ok": true, "payload": { "type": "hello-ok", "protocol": 3 } }
```

**Sending an agent message after auth:**
```json
{
  "type": "req",
  "id": "msg-id",
  "method": "sessions.send",
  "params": {
    "sessionKey": "session-identifier",
    "content": "user message text"
  }
}
```

**Assessment:** The handshake requires cryptographic key signing (`publicKey` + `signature` + device `id`). These are generated during OpenClaw's initial device pairing flow and are not trivially reproducible by an external Python daemon without access to the stored key material. The Gateway's security model is intentionally hostile to unauthenticated external callers (CVE-2026-25253 was a cross-site WebSocket hijacking vulnerability; the 2026.2.19-2 hardening update made the auth requirement even stricter).

**Verdict:** WebSocket direct injection is **not recommended** for cal-trigger v1. The CLI adapter is the correct abstraction — it handles auth transparently by reusing the Gateway's paired session state. The WS path could be explored in v2 if latency becomes a bottleneck and users are comfortable exposing their device key material.

---

### HTTP Webhook Surface

OpenClaw's Gateway exposes three HTTP POST routes for external trigger use:

| Endpoint | Purpose |
|----------|---------|
| `POST /hooks/wake` | Enqueue a system event for the main session |
| `POST /hooks/agent` | Trigger an isolated agent turn |
| `POST /hooks/<name>` | Custom hook, resolved via `hooks.mappings` config |

**`POST /hooks/agent` — the relevant endpoint:**

```bash
curl -X POST http://127.0.0.1:18789/hooks/agent \
  -H "Authorization: Bearer <hook-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Research competitor pricing",
    "agentId": "my-agent",
    "name": "calendar-trigger",
    "timeoutSeconds": 120
  }'
```

**Auth:** All hook requests require `Authorization: Bearer <token>` or `x-openclaw-token: <header>`. Query-string tokens are rejected. The token is separate from the WS device token and is configured in `openclaw.yml` under `hooks.token`.

**Known issues:**

- GitHub issue #12874 (referenced in the task spec) reported `POST /api/sessions/spawn` returning 405 — this endpoint is the in-session `sessions_spawn` tool, not the public webhook surface. It was never meant for external use; the correct external endpoint is `/hooks/agent`.
- GitHub issue #48212 (March 2026, v2026.3.13): All HTTP POST routes to the Gateway return 404. Only GET and WebSocket connections work. This is a **confirmed active regression** as of this writing.
- The webhook surface requires a configured `hooks.token` in `openclaw.yml` — this adds a setup step that the CLI approach avoids.

**Verdict:** The `/hooks/agent` endpoint is architecturally the right approach and ~200–400 ms faster than the CLI fork. However, the active 404 regression in v2026.3.13 makes it **unreliable for production use right now**. The cal-trigger `http` adapter can target this endpoint when the regression is resolved, but the `openclaw` adapter (CLI path) should remain the primary integration.

---

### Cron Trigger

OpenClaw has a native cron scheduling system (`openclaw cron add`) that can run isolated agent sessions on a schedule.

**Command syntax:**

```bash
# Standard cron schedule (isolated session, delivers to Slack)
openclaw cron add \
  --name "calendar-poll" \
  --cron "* * * * *" \
  --session isolated \
  --message "Check calendar for upcoming agent tasks and dispatch" \
  --announce \
  --channel slack \
  --to "channel:C123456"

# Interval-based
openclaw cron add --name "every-minute" --every "1m" --session isolated --message "…"

# One-shot (auto-deletes after run)
openclaw cron add --name "onetime" --at "20m" --message "…" --delete-after-run
```

**Session types:**

| Type | Behavior |
|------|---------|
| `isolated` | Fresh context per run (recommended for polling tasks) |
| `main` | Injects system event into the primary session |
| `current` | Reuses the currently active session |

**Additional options:** `--tz "America/Los_Angeles"`, `--thinking <level>`, `--model <model-id>`, `--stagger <window>` or `--exact` for timing precision.

**Assessment of using OpenClaw cron as the polling loop:**

| Pro | Con |
|-----|-----|
| No external daemon process | Hard-couples cal-trigger to OpenClaw |
| Managed restarts, jitter control | Agent turn needed just to poll iCal (wastes tokens/API calls for no-op polls) |
| `--announce` for free delivery routing | Cron runs in isolated session — no shared state across runs without external persistence |
| Works without a separate Python install | Harder to test independently; requires OpenClaw running |

**Verdict:** Native OpenClaw cron is **not recommended as the polling mechanism**. The cal-trigger daemon is intentionally framework-agnostic; building the poll loop inside OpenClaw would prevent HTTP, shell, and Python adapter users from using it. The correct model is: **cal-trigger daemon owns the polling loop and state tracking; adapters own the delivery method.**

The `openclaw cron` system could be used as an *alternative way to install cal-trigger* for OpenClaw-only users who want zero external processes (advanced use case, document in README).

---

### OpenClaw Summary and Recommendation

| Path | Status | Latency | Complexity | Recommendation |
|------|--------|---------|------------|----------------|
| `openclaw agent` CLI | Working | 600 ms–1.4 s overhead | Low | **Use as primary** |
| `/hooks/agent` HTTP | Regressed (v2026.3.13) | ~100–200 ms overhead | Medium | Use in v2 when fixed |
| WebSocket direct | Underdocumented, auth complexity | ~10 ms overhead | High | Defer to v2+ |
| Native cron loop | Architecturally wrong for this use case | N/A | Medium | Not recommended |

---

## 0b. Orbital Trigger Mechanism

**Status:** Orbital repo URL was provided (`https://github.com/zqiren/Orbital`) but trigger mechanism docs were not available at research time (`{{ORBITAL_TRIGGER_NOTES}}` placeholder not filled).

**What was determined from the repo:**

- Orbital runs a **FastAPI daemon** (default port 8000) with REST + WebSocket communication between frontend and agent backend.
- API factory lives at `agent_os/api/app.py`.
- Supports schedule triggers (cron expressions with timezone) and file-watch triggers — created through natural language chat with a management agent, which translates to internal `create_trigger` tool calls.
- No public documentation found for a direct "POST task to run an agent" REST endpoint. The system appears UI-driven with the REST layer used for frontend communication, not external script invocation.
- Real-time agent streaming uses WebSocket tunnels; a cloud relay layer handles REST proxying and event forwarding.

**Implication for cal-trigger:**

The `http` adapter is the correct abstraction for Orbital once its external trigger API is documented or its FastAPI routes are confirmed. The interface to implement is:

```python
# http adapter config for Orbital (speculative — endpoint path TBD)
trigger:
  type: http
  url: "http://localhost:8000/api/v1/trigger"   # confirm actual path with Orbital team
  method: POST
  headers:
    Authorization: "Bearer <orbital-token>"
  # body will be the task dict: {title, description, start, end, uid}
```

**Adapter interface for future Orbital-specific adapter:**

If Orbital exposes a richer API (agent selection, model routing, async run tracking), a dedicated `orbital` adapter can be added following the same pattern as `openclaw`:

```python
# adapters/orbital.py (stub for v2)
def trigger(task: dict, config: dict) -> bool:
    """
    Orbital adapter — calls the Orbital FastAPI daemon.
    Falls back to the generic http adapter until the Orbital API is stable.
    """
    import requests
    url = config["url"]  # e.g., http://localhost:8000/api/v1/run
    headers = {"Authorization": f"Bearer {config.get('token', '')}"}
    resp = requests.post(url, json=task, headers=headers, timeout=30)
    return resp.ok
```

**Conclusion:** The `http` adapter fully covers the Orbital integration path for v1. A dedicated `orbital` adapter can be added in v2 once the HTTP API surface is confirmed.

---

## 0c. Generic Agent Trigger Patterns

### CrewAI

**Self-hosted (open-source) — Python API:**

```python
from crewai import Crew, Agent, Task

crew = Crew(agents=[...], tasks=[...])

# Synchronous kickoff with inputs dict
result = crew.kickoff(inputs={
    "title": "Research competitor pricing",
    "description": "Compare Cursor, Windsurf, Bolt...",
    "start": "2026-04-08T14:00:00Z",
    "uid": "abc123@google.com"
})

# Async kickoff (v0.9+)
result = await crew.kickoff_async(inputs={...})
```

**CrewAI Enterprise / AMP — REST API:**

```bash
# Kick off a deployed crew
POST https://<crew-url>.crewai.com/kickoff
Authorization: Bearer <token>
Content-Type: application/json

{"inputs": {"title": "Research competitor pricing", ...}}

# Response
{"kickoff_id": "run_abc123"}

# Poll status
GET https://<crew-url>.crewai.com/status/run_abc123
```

**Webhook callbacks:** The kickoff endpoint supports `taskWebhookUrl` and `stepWebhookUrl` for event-driven result delivery.

**Cal-trigger adapter:** The `python` adapter is the right fit for self-hosted CrewAI:

```python
# adapters/python_adapter.py usage
trigger:
  type: python
  module: "my_crew_adapter"      # user-created module
  function: "handle_task"        # called with task dict

# my_crew_adapter.py (user-created)
def handle_task(task: dict) -> bool:
    from my_crew import build_crew
    crew = build_crew()
    result = crew.kickoff(inputs=task)
    return bool(result)
```

For CrewAI Enterprise, use the `http` adapter pointing at the `/kickoff` endpoint.

---

### LangGraph

**LangGraph Platform / langgraph-api (recommended for production):**

Start the local server:
```bash
langgraph dev --port 2024   # or: langgraph up
```

Trigger a run via HTTP:
```bash
# Streaming run (primary pattern)
POST http://localhost:2024/runs/stream
Content-Type: application/json

{
  "assistant_id": "my-agent",
  "input": {
    "messages": [{"role": "human", "content": "Research competitor pricing"}]
  },
  "stream_mode": "messages-tuple"
}
```

**Python SDK (in-process):**
```python
from langgraph.pregel import Pregel

# Direct graph invocation
graph = build_graph()
result = await graph.ainvoke({"messages": [("human", task["title"])]})

# Or via LangGraph client (when server is running)
from langgraph_sdk import get_async_client
client = get_async_client(url="http://localhost:2024")
async for event in client.runs.stream(
    thread_id=None,
    assistant_id="my-agent",
    input={"messages": [{"role": "human", "content": task["title"]}]},
):
    print(event)
```

**FastAPI wrapper (self-hosted alternative):**

When not using `langgraph-api`, the common pattern is wrapping the graph in a FastAPI endpoint:
```python
from fastapi import FastAPI
app = FastAPI()

@app.post("/trigger")
async def trigger_agent(task: dict):
    result = await graph.ainvoke({"messages": [("human", task["title"])]})
    return {"result": result}
```

**Cal-trigger adapter:**
- Use `http` adapter for LangGraph Platform (`POST /runs/stream`) or any FastAPI wrapper
- Use `python` adapter for direct in-process graph invocation

```yaml
# http adapter for LangGraph Platform
trigger:
  type: http
  url: "http://localhost:2024/runs/stream"
  method: POST
  headers:
    Content-Type: "application/json"
```

---

### AutoGen / Microsoft Agent Framework

**AutoGen v0.4 (agentchat) — async Python API:**

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination

agent = AssistantAgent("assistant", model_client=...)
team = RoundRobinGroupChat([agent], termination_condition=TextMentionTermination("TERMINATE"))

# Trigger a single run
async def run_task(task: dict):
    await team.run(task=task["title"])

asyncio.run(run_task(task))

# Streaming alternative (more control over external interruption)
async def run_task_streaming(task: dict):
    async for msg in team.run_stream(task=task["title"]):
        print(msg)
```

**Microsoft Agent Framework v1.0 (Python, late 2025+):**

The Agent Framework (merger of AutoGen + Semantic Kernel) uses a workflow-builder pattern:

```python
from agent_framework import WorkflowBuilder, AgentResponseUpdate
from agent_framework.foundry import FoundryChatClient

client = FoundryChatClient(project_endpoint=..., model=..., credential=...)
agent = client.as_agent(name="Researcher", instructions="...")
workflow = WorkflowBuilder(start_executor=agent).build()

# External trigger via workflow.run()
events = workflow.run(task["title"], stream=True)
async for event in events:
    if event.type == "output" and isinstance(event.data, AgentResponseUpdate):
        print(event.data.text, end="", flush=True)
```

**HTTP trigger (AutoGen + FastAPI wrapper):**

AutoGen does not expose a built-in HTTP server. The standard community pattern wraps agents in FastAPI:
```python
from fastapi import FastAPI
app = FastAPI()

@app.post("/trigger")
async def trigger(task: dict):
    background_tasks.add_task(run_task, task)
    return {"status": "queued"}
```

**Cal-trigger adapter:**
- Use `python` adapter for direct AutoGen/Agent Framework invocation
- Use `http` adapter if the user has wrapped their agents in FastAPI/similar

---

### Claude Code

**CLI one-shot (confirmed working):**

```bash
# -p / --print: run non-interactively, print output, exit
claude -p "Research competitor pricing for AI coding tools"

# With a specific working directory
claude -p "Summarize the task: <task>" --cwd /path/to/project

# Capture output
output=$(claude -p "Complete this task: $task_description")
```

**Behavior notes:**
- `claude -p` is primarily a **coding-focused tool** (file editing, bash execution, code analysis), not a general agent dispatcher.
- It executes with access to the filesystem and bash, which is powerful but may be more than needed for a pure dispatch use case.
- Process fork overhead: similar to OpenClaw CLI (~300–500 ms for Node.js startup).
- Non-interactive mode aborts if the safety classifier repeatedly blocks actions with no user fallback.

**Sub-agents (claude --agent, available in recent versions):**
```bash
# Named sub-agents with custom system prompts
claude --agent=my-agent -p "Complete this calendar task"
```

**Cal-trigger adapter:** Use the `shell` adapter with the `claude -p` command:

```yaml
trigger:
  type: shell
  command: "claude -p '{title}: {description}'"
```

**Verdict:** Claude Code is a viable `shell` adapter target for users who want coding-capable agents. Not recommended as the primary integration path for general agent dispatch (the OpenClaw, HTTP, and Python adapters are more appropriate for that).

---

## Adapter Coverage Matrix

| Framework / Platform | Adapter | Notes |
|---------------------|---------|-------|
| OpenClaw | `openclaw` (first-class) | Direct CLI adapter, most reliable |
| OpenClaw (webhook) | `http` | Viable when `/hooks/agent` regression is fixed |
| Orbital | `http` | FastAPI at port 8000; specific endpoint TBD |
| CrewAI (self-hosted) | `python` | `crew.kickoff(inputs=task)` |
| CrewAI Enterprise | `http` | `POST /kickoff` REST API |
| LangGraph Platform | `http` | `POST /runs/stream` via langgraph-api |
| LangGraph (in-process) | `python` | `graph.ainvoke(...)` |
| AutoGen v0.4 | `python` | `await team.run(task=...)` |
| Microsoft Agent Framework | `python` | `workflow.run(task, stream=True)` |
| Claude Code | `shell` | `claude -p "..."` one-shot |
| Any HTTP-serving agent | `http` | Generic POST with task JSON body |
| Any shell-callable agent | `shell` | Template substitution, `subprocess.run` |
| Custom Python agent | `python` | `module.function(task_dict)` |

**Coverage assessment:**

The four adapter types — `shell`, `openclaw`, `http`, `python` — provide complete coverage of the known agent ecosystem:

- **`openclaw`** handles the primary deployment target (OpenClaw users) with first-class argument handling, `--deliver` routing, and proper escaping.
- **`http`** covers every framework that can be placed behind an HTTP server (Orbital, LangGraph Platform, CrewAI Enterprise, any FastAPI-wrapped agent, future Orbital API). This is the highest-breadth adapter.
- **`python`** covers in-process frameworks (CrewAI self-hosted, LangGraph in-process, AutoGen, Microsoft Agent Framework) without requiring a server.
- **`shell`** is the universal fallback: if it can be invoked from the command line, this adapter covers it (Claude Code, any CLI-first agent, `curl` one-liners).

No fifth adapter type is needed for v1. A future `orbital` adapter could be added as a thin wrapper around `http` once Orbital's API is stable.

---

## Sources

- [OpenClaw Gateway Protocol docs](https://docs.openclaw.ai/gateway/protocol)
- [OpenClaw CLI agent command docs](https://docs.openclaw.ai/cli/agent)
- [OpenClaw Webhook / Scheduled Tasks docs](https://docs.openclaw.ai/automation/webhook)
- [OpenClaw Cron Jobs docs](https://docs.openclaw.ai/automation/cron-jobs)
- [OpenClaw Architecture Overview (ppaolo.substack.com)](https://ppaolo.substack.com/p/openclaw-system-architecture-overview)
- [OpenClaw Gateway port configuration (skywork.ai)](https://skywork.ai/skypage/en/openclaw-gateway-port-configuration/2037015899148128257)
- [OpenClaw HTTP POST webhook routes return 404 — Issue #48212](https://github.com/openclaw/openclaw/issues/48212)
- [OpenClaw HTTP API for spawning subagent sessions — Issue #15342](https://github.com/openclaw/openclaw/issues/15342)
- [OpenClaw WebSocket auth fails — Issue #19517](https://github.com/openclaw/openclaw/issues/19517)
- [OpenClaw CLI latency — Issue #15459](https://github.com/openclaw/openclaw/issues/15459)
- [OpenClaw cron scheduler guide (LumaDock)](https://lumadock.com/tutorials/openclaw-cron-scheduler-guide)
- [OpenClaw webhooks explained (LumaDock)](https://lumadock.com/tutorials/openclaw-webhooks-explained)
- [Cron vs Heartbeat (openclaws.io)](https://openclaws.io/docs/automation/cron-vs-heartbeat)
- [OpenClaw npm package](https://www.npmjs.com/package/openclaw)
- [OpenClaw security architecture (nebius.com)](https://nebius.com/blog/posts/openclaw-security)
- [Orbital GitHub repo](https://github.com/zqiren/Orbital)
- [CrewAI Kickoff Crew docs](https://docs.crewai.com/en/enterprise/guides/kickoff-crew)
- [CrewAI Deployed Crew API docs](https://docs.crewai.com/enterprise/guides/use-crew-api)
- [CrewAI community: Crew.kickoff inputs](https://community.crewai.com/t/crew-kickoff-doesnt-pass-inputs-argument-into-agent/572/12)
- [LangGraph local server docs](https://docs.langchain.com/oss/python/langgraph/local-server)
- [LangGraph Platform API reference](https://langchain-ai.github.io/langgraph/cloud/reference/api/api_ref.html)
- [LangGraph GitHub](https://github.com/langchain-ai/langgraph)
- [AutoGen Teams docs](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/tutorial/teams.html)
- [AutoGen GitHub](https://github.com/microsoft/autogen)
- [Microsoft Agent Framework Overview](https://learn.microsoft.com/en-us/agent-framework/overview/)
- [Microsoft Agent Framework: Agents in Workflows](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/using-agents)
- [Microsoft Agent Framework v1.0 release blog](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/)
- [Claude Code best practices](https://code.claude.com/docs/en/best-practices)
- [MindStudio: Building scheduled agents with Claude Code](https://www.mindstudio.ai/blog/how-to-build-scheduled-ai-agents-claude-code)
- [BentoML: Deploying LangGraph agent as REST API](https://www.bentoml.com/blog/deploying-a-langgraph-agent-application-with-an-open-source-model)
