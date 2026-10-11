
import psutil

from app.tools.result import make_tool_result


def get_disk_usage() -> dict:
    """Return current disk usage as a structured tool result."""
    try:
        disk = psutil.disk_usage("/")

        return make_tool_result(
            tool="get_disk_usage",
            status="success",
            data={
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "usage_percent": disk.percent,
            },
        )

    except Exception as error:
        return make_tool_result(
            tool="get_disk_usage",
            status="error",
            data={},
            error={
                "code": "DISK_READ_ERROR",
                "message": str(error),
            },
        )
