
import psutil

from app.tools.result import make_tool_result


def get_memory_usage() -> dict:
    try:
        memory = psutil.virtual_memory()

        return make_tool_result(
            tool="get_memory_usage",
            status="success",
            data={
                "total_bytes": memory.total,
                "used_bytes": memory.used,
                "available_bytes": memory.available,
                "usage_percent": memory.percent,
            },
        )

    except Exception as error:
        return make_tool_result(
            tool="get_memory_usage",
            status="error",
            data={},
            error={
                "code": "MEMORY_READ_ERROR",
                "message": str(error),
            },
        )
