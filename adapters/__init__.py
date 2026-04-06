"""Adapter factory for cal_trigger dispatch."""


def get_adapter(config: dict):
    """Return adapter function based on trigger config type."""
    adapter_type = config.get("type", "shell")
    if adapter_type == "shell":
        from .shell import trigger
    elif adapter_type == "openclaw":
        from .openclaw import trigger
    elif adapter_type == "http":
        from .http import trigger
    elif adapter_type == "python":
        from .python_adapter import trigger
    else:
        raise ValueError(f"Unknown adapter type: {adapter_type}")
    return trigger
