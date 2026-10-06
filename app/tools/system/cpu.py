import psutil


def get_cpu_usage() -> dict:
    """Return current CPU usage as a structured tool result."""
    try:
        usage = psutil.cpu_percent(interval=1)

        return {
            "tool": "get_cpu_usage",
            "status": "success",
            "data": {
                "usage_percent": usage
            }
        }

    except Exception as error:
        return {
            "tool": "get_cpu_usage",
            "status": "error",
            "data": {},
            "error": str(error)
        }
