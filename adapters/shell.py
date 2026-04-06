"""Shell adapter — runs a configurable shell command for each triggered event."""

import logging
import shlex
import subprocess

logger = logging.getLogger(__name__)


def trigger(task: dict, config: dict) -> bool:
    """
    Execute a shell command with task fields interpolated.

    Config keys:
        command (str): Shell command template. Supports {title}, {description},
                       {start}, {end}, {uid} placeholders.

    Returns True on exit code 0, False otherwise.
    """
    command_template = config.get("command", "echo '{title}'")
    try:
        command = command_template.format(**task)
    except KeyError as exc:
        logger.error("Shell adapter: unknown placeholder %s in command template", exc)
        return False

    args = shlex.split(command)
    logger.info("Shell adapter: running command: %s", args)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info("Shell adapter stdout: %s", result.stdout.strip())
        if result.stderr:
            logger.warning("Shell adapter stderr: %s", result.stderr.strip())
        if result.returncode != 0:
            logger.error("Shell adapter: command exited with code %d", result.returncode)
            return False
        return True
    except OSError as exc:
        logger.error("Shell adapter: failed to execute command: %s", exc)
        return False
