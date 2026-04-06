"""Python function adapter — imports a user module and calls a handler function."""

import importlib
import logging

logger = logging.getLogger(__name__)


def trigger(task: dict, config: dict) -> bool:
    """
    Import a module and call a function with the task dict.

    Config keys:
        module (str, required): module to import (e.g. "my_adapter")
        function (str): function name to call (default: "handle_task")

    The called function must accept a single ``task: dict`` argument.
    Returns True if the function returns truthy or None, False on exception or
    if the function explicitly returns False.
    """
    module_name = config.get("module")
    if not module_name:
        logger.error("Python adapter: 'module' is required in config")
        return False

    func_name = config.get("function", "handle_task")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.error("Python adapter: could not import module '%s': %s", module_name, exc)
        return False

    func = getattr(module, func_name, None)
    if func is None:
        logger.error(
            "Python adapter: function '%s' not found in module '%s'", func_name, module_name
        )
        return False

    logger.info("Python adapter: calling %s.%s", module_name, func_name)
    try:
        result = func(task)
        if result is False:
            logger.warning("Python adapter: %s.%s returned False", module_name, func_name)
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Python adapter: %s.%s raised an exception: %s", module_name, func_name, exc)
        return False
