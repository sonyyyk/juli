
import psutil

from app.tools.result import make_tool_result


def get_cpu_usage() -> dict:
    """Return current CPU usage as a structured tool result."""
    try:
        usage = psutil.cpu_percent(interval=1)

        return make_tool_result(
            tool="get_cpu_usage",
            status="success",
            data={"usage_percent": usage},
        )

    except Exception as error:
        return make_tool_result(
            tool="get_cpu_usage",
            status="error",
            data={},
            error={
                "code": "CPU_READ_ERROR",
                "message": str(error),
            },
        )
