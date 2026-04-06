# Smoke Test Results

**Date:** 2026-04-07  
**Platform:** Windows 10 (Python 3.13)  
**Calendar:** zqzqzqr0@gmail.com (Google Calendar, Asia/Singapore timezone)  
**iCal feed:** Private iCal URL for the above calendar

---

## Test Configuration

- `poll_interval`: 15s (reduced from 60s for faster testing)
- `lookahead_minutes`: 30 (increased from 5 for test flexibility)
- `event_prefix`: `[agent]`

---

## Test Results

| Step | Action | Expected Result | Actual Result | Status |
|------|--------|-----------------|---------------|--------|
| 1 | Create `config.yml` with iCal URL, `trigger.type: shell`, command: `echo` | Config loads without error | Config loaded, daemon started with correct parameters | PASS |
| 2 | Create Google Calendar event: `[agent] Test task from calendar`, start within 5 min | Event visible in calendar | Event created via Google Calendar API (id: `fp2olj644h5edg7ha048fkjh74`) | PASS |
| 3 | Run `cal_trigger.py` | Daemon starts, begins polling | Daemon started: `cal_trigger starting - prefix='[agent]', lookahead=30m, interval=15s` | PASS |
| 4 | Wait for poll cycle | Daemon detects event, prints parsed task metadata via echo | `Poll complete - 1 matching event(s)`, `Shell adapter stdout: Triggered: Test task from calendar` | PASS |
| 5 | Check `dispatched.json` | Event UID is recorded | `fp2olj644h5edg7ha048fkjh74@google.com::2026-04-06` present in dispatched set | PASS |
| 6 | Wait for next poll cycle | Same event is NOT re-triggered | Second poll: `1 matching event(s)` found but no trigger logged — dedup working | PASS |
| 7 | Create second event `[agent] Second test` | Triggers without daemon restart | New daemon run detected 2 matching events, triggered only "Second test" (first already dispatched) | PASS |
| 8 | Create non-agent event `Regular meeting - no agent prefix` | Event is ignored | Only 2 `[agent]`-prefixed events matched; non-agent event correctly filtered out | PASS |
| 9 | Switch to `trigger.type: openclaw` | `openclaw agent` invoked with correct args | SKIPPED — OpenClaw not installed on test machine |
| 10 | Switch to `trigger.type: http`, run local HTTP server | POST received with correct JSON payload | HTTP server received: `{"title": "Second test", "description": "Second smoke test event.", "start": "2026-04-06T18:55:00+00:00", "end": "2026-04-06T19:25:00+00:00", "uid": "i4ovaiuc2gmaovm09bavu0qbec@google.com"}` | PASS |
| 11 | Configure trigger to failing command, create agent event | Retries on next poll cycle, not added to dispatched | Adapter failure logged: `will retry next cycle`. Events retried on every subsequent poll. Not added to dispatched.json. | PASS |

---

## Summary

**10/11 tests passed.** Step 9 (OpenClaw adapter) skipped — requires OpenClaw installation.

### Key Observations

1. **iCal feed latency:** Google Calendar's iCal export reflected new events within ~2 seconds of creation via the API. Effectively real-time for a 60s poll interval.

2. **Deduplication works correctly:** The compound key (`uid::YYYY-MM-DD`) prevents re-triggering across daemon restarts.

3. **Failure retry behavior verified:** When the adapter returns `False` (or raises an exception), the event is NOT added to `dispatched.json` and is retried on the next poll cycle.

4. **HTTP adapter payload format:** Clean JSON with all five fields (`title`, `description`, `start`, `end`, `uid`). Title has `[agent]` prefix correctly stripped.

5. **Non-agent event filtering:** Events without the `[agent]` prefix are silently ignored — no log noise.

### Known Issues

- **Windows encoding:** Chinese-locale Windows systems produce `UnicodeEncodeError` when logging error messages containing Chinese characters (e.g., `FileNotFoundError` messages). This is a Python stdout encoding issue, not a daemon bug. Workaround: set `PYTHONIOENCODING=utf-8` or use `chcp 65001`.

- **`exit 1` on Windows:** The `exit` command is a shell builtin, not a standalone executable. On Windows, `subprocess.run(["exit", "1"])` raises `FileNotFoundError`. Use `python -c "import sys; sys.exit(1)"` for cross-platform failure testing.
