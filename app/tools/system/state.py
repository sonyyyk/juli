from typing import Any, Callable

from app.tools.result import ToolResult, make_tool_result
from app.tools.system.cpu import get_cpu_usage
from app.tools.system.memory import get_memory_usage
from app.tools.system.disk import get_disk_usage


def get_system_state() -> ToolResult:
    """Return the current system state using system tools.

    Status is aggregated from the CPU, memory and disk tools: ``success`` when
    all succeed, ``partial`` when some fail (the failed resource is ``None``),
    and ``error`` when all fail.
    """
    # Looked up at call time so each tool can be replaced independently.
    readers: dict[str, Callable[[], Any]] = {
        "cpu": get_cpu_usage,
        "memory": get_memory_usage,
        "disk": get_disk_usage,
    }

    data: dict[str, dict[str, Any] | None] = {}
    failures: dict[str, dict[str, str]] = {}

    for resource, reader in readers.items():
        data[resource], failure = _read_resource(reader)
        if failure is not None:
            failures[resource] = failure

    if not failures:
        return make_tool_result("get_system_state", "success", data=data)

    summary = "; ".join(
        f"{resource}: {failure['code']}" for resource, failure in failures.items()
    )

    if len(failures) == len(readers):
        return make_tool_result(
            "get_system_state",
            "error",
            error={
                "code": "SYSTEM_STATE_UNAVAILABLE",
                "message": f"All system resources failed: {summary}",
                "details": failures,
            },
        )

    return make_tool_result(
        "get_system_state",
        "partial",
        data=data,
        error={
            "code": "SYSTEM_STATE_PARTIAL",
            "message": f"Some system resources failed: {summary}",
            "details": failures,
        },
    )


def _read_resource(
    reader: Callable[[], Any],
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Run one tool and return (data, failure); exactly one of them is None."""
    try:
        result = reader()
    except Exception as exc:
        return None, {"code": "TOOL_EXCEPTION", "message": str(exc)}

    if not isinstance(result, dict):
        return None, {
            "code": "INVALID_TOOL_RESULT",
            "message": "Tool result must be a mapping.",
        }

    if result.get("status") == "success" and isinstance(result.get("data"), dict):
        return result["data"], None

    error = result.get("error")
    if isinstance(error, dict):
        return None, {
            "code": str(error.get("code", "UNKNOWN_ERROR")),
            "message": str(error.get("message", "")),
        }
    return None, {
        "code": "INVALID_TOOL_RESULT",
        "message": f"Tool returned status {result.get('status')!r} without usable data.",
    }
