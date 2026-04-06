"""HTTP adapter — sends the task as a JSON payload via HTTP POST/PUT."""

import logging

import requests

logger = logging.getLogger(__name__)


def trigger(task: dict, config: dict) -> bool:
    """POST/PUT task metadata as JSON to a URL. Returns True on 2xx status."""
    url = config.get("url")
    if not url:
        logger.error("HTTP adapter: 'url' is required in config")
        return False

    method = config.get("method", "POST").upper()
    headers = config.get("headers", {})
    timeout = config.get("timeout", 30)

    logger.info("HTTP adapter: %s %s", method, url)
    try:
        response = requests.request(
            method,
            url,
            json=task,
            headers=headers,
            timeout=timeout,
        )
        if response.ok:
            logger.info("HTTP adapter: received status %d", response.status_code)
            return True
        logger.error(
            "HTTP adapter: request failed with status %d: %s",
            response.status_code,
            response.text[:200],
        )
        return False
    except requests.RequestException as exc:
        logger.error("HTTP adapter: request error: %s", exc)
        return False
