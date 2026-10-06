import psutil


def get_memory_usage() -> dict:
    """Return current memory usage as a structured tool result."""
    try:
        memory = psutil.virtual_memory()

        return {
            "tool": "get_memory_usage",
            "status": "success",
            "data": {
                "total_bytes": memory.total,
                "used_bytes": memory.used,
                "available_bytes": memory.available,
                "usage_percent": memory.percent,
            },
        }

    except Exception as error:
        return {
            "tool": "get_memory_usage",
            "status": "error",
            "data": {},
            "error": str(error),
        }
