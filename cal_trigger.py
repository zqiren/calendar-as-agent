"""cal_trigger.py — Calendar-to-agent daemon.

Polls a Google Calendar iCal feed and invokes a configured trigger adapter
whenever it detects events whose title starts with a configured prefix and
whose start time falls within a configurable lookahead window.
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml
from icalendar import Calendar

from adapters import get_adapter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yml"
DISPATCHED_PATH = BASE_DIR / "dispatched.json"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load and return the YAML configuration file."""
    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    # Apply defaults
    config.setdefault("event_prefix", "[agent]")
    config.setdefault("poll_interval", 60)
    config.setdefault("lookahead_minutes", 5)
    return config


# ---------------------------------------------------------------------------
# Dispatched-state helpers
# ---------------------------------------------------------------------------


def load_dispatched(path: Path = DISPATCHED_PATH) -> set:
    """Return set of already-dispatched compound keys (uid::date)."""
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return set(data.get("dispatched", []))


def save_dispatched(dispatched: set, path: Path = DISPATCHED_PATH) -> None:
    """Persist the dispatched set to disk."""
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"dispatched": sorted(dispatched)}, fh, indent=2)


def dispatch_key(uid: str, start: datetime) -> str:
    """Compound key: uid::YYYY-MM-DD — unique per event occurrence."""
    return f"{uid}::{start.date().isoformat()}"


# ---------------------------------------------------------------------------
# iCal fetching & parsing
# ---------------------------------------------------------------------------


def fetch_ical(url: str, timeout: int = 15) -> bytes:
    """Download the iCal feed and return raw bytes."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def to_datetime(value) -> datetime:
    """Normalise an icalendar date/datetime value to a timezone-aware datetime."""
    import datetime as dt_module
    # vDDDTypes and similar wrappers all expose a .dt attribute
    dt = value.dt if hasattr(value, "dt") else value
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    # Plain date (all-day event) — treat midnight UTC as the start
    if isinstance(dt, dt_module.date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    raise TypeError(f"Cannot convert {type(value)} to datetime")


def parse_events(raw: bytes, prefix: str, now: datetime, lookahead: timedelta) -> list:
    """
    Parse iCal bytes and return a list of task dicts for matching events.

    Matching criteria:
    - SUMMARY starts with ``prefix`` (case-sensitive)
    - DTSTART is within (now, now + lookahead]
    """
    cal = Calendar.from_ical(raw)
    tasks = []
    window_end = now + lookahead

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        try:
            summary = str(component.get("SUMMARY", ""))
            if not summary.startswith(prefix):
                continue

            dtstart_raw = component.get("DTSTART")
            dtend_raw = component.get("DTEND")
            uid = str(component.get("UID", ""))
            description = str(component.get("DESCRIPTION", ""))

            if dtstart_raw is None:
                logger.warning("Event '%s' has no DTSTART, skipping", summary)
                continue

            start = to_datetime(dtstart_raw)
            end = to_datetime(dtend_raw) if dtend_raw else start

            # Only trigger events within the lookahead window
            if not (now < start <= window_end):
                continue

            title = summary[len(prefix):].strip()
            tasks.append({
                "title": title,
                "description": description,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "uid": uid,
                "_start_dt": start,  # internal — removed before dispatch
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed event: %s", exc)

    return tasks


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------


def poll_once(config: dict, dispatched: set, adapter_fn) -> set:
    """
    Perform one poll cycle.  Returns the (possibly updated) dispatched set.
    """
    prefix = config["event_prefix"]
    lookahead = timedelta(minutes=config["lookahead_minutes"])
    now = datetime.now(tz=timezone.utc)

    try:
        raw = fetch_ical(config["ical_url"])
    except requests.RequestException as exc:
        logger.warning("Network error fetching iCal feed: %s", exc)
        return dispatched

    try:
        tasks = parse_events(raw, prefix, now, lookahead)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse iCal data: %s", exc)
        return dispatched

    logger.info("Poll complete — %d matching event(s) in lookahead window", len(tasks))

    for task in tasks:
        start_dt = task.pop("_start_dt")
        key = dispatch_key(task["uid"], start_dt)

        if key in dispatched:
            logger.debug("Already dispatched: %s", key)
            continue

        logger.info("Triggering event: '%s' (uid=%s, start=%s)", task["title"], task["uid"], task["start"])
        try:
            success = adapter_fn(task, config.get("trigger", {}))
        except Exception as exc:  # noqa: BLE001
            logger.error("Adapter raised an exception for '%s': %s", task["title"], exc)
            success = False

        if success:
            dispatched.add(key)
            save_dispatched(dispatched)
            logger.info("Dispatched and recorded: %s", key)
        else:
            logger.warning("Adapter reported failure for '%s' — will retry next cycle", task["title"])

    return dispatched


def run(config_path: Path = CONFIG_PATH) -> None:
    """Load config and run the polling loop until interrupted."""
    config = load_config(config_path)
    adapter_fn = get_adapter(config.get("trigger", {}))
    dispatched = load_dispatched()

    poll_interval = config["poll_interval"]
    logger.info(
        "cal_trigger starting — prefix='%s', lookahead=%dm, interval=%ds",
        config["event_prefix"],
        config["lookahead_minutes"],
        poll_interval,
    )

    try:
        while True:
            dispatched = poll_once(config, dispatched, adapter_fn)
            logger.info("Sleeping %ds until next poll…", poll_interval)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Shutdown requested — exiting cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()
