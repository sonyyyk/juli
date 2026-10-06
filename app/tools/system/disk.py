import psutil


def get_disk_usage() -> dict:
    """Return current disk usage as a structured tool result."""
    try:
        disk = psutil.disk_usage("/")

        return {
            "tool": "get_disk_usage",
            "status": "success",
            "data": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "usage_percent": disk.percent,
            },
        }

    except Exception as error:
        return {
            "tool": "get_disk_usage",
            "status": "error",
            "data": {},
            "error": str(error),
        }
