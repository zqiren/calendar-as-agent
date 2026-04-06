"""OpenClaw adapter — invokes the openclaw CLI agent for each triggered event."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def trigger(task: dict, config: dict) -> bool:
    """Call openclaw agent CLI with task title+description. Returns True on exit code 0."""
    agent = config.get("agent", "default")
    parts = [p for p in (task.get("title", ""), task.get("description", "")) if p]
    message = ": ".join(parts) or "(no description)"

    args = ["openclaw", "agent", "--agent", agent, "--message", message]

    if config.get("local"):
        args.append("--local")

    if config.get("deliver"):
        args.append("--deliver")
        if config.get("reply_channel"):
            args.extend(["--reply-channel", config["reply_channel"]])
        if config.get("reply_to"):
            args.extend(["--reply-to", config["reply_to"]])

    timeout = config.get("timeout", 30)
    logger.info("OpenClaw adapter: agent=%s, local=%s, deliver=%s",
                agent, config.get("local"), config.get("deliver"))
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.stdout:
            logger.info("OpenClaw adapter stdout: %s", result.stdout.strip())
        if result.stderr:
            logger.warning("OpenClaw adapter stderr: %s", result.stderr.strip())
        if result.returncode != 0:
            logger.error("OpenClaw adapter: command exited with code %d", result.returncode)
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("OpenClaw adapter: command timed out after %ds", timeout)
        return False
    except OSError as exc:
        logger.error("OpenClaw adapter: failed to execute command: %s", exc)
        return False
