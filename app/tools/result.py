
from typing import Any, Literal, TypedDict


ToolStatus = Literal["success", "partial", "error"]


class ToolError(TypedDict):
    code: str
    message: str


class ToolResult(TypedDict):
    tool: str
    status: ToolStatus
    data: dict[str, Any] | None
    error: ToolError | None


def make_tool_result(
    tool: str,
    status: ToolStatus,
    data: dict[str, Any] | None = None,
    error: ToolError | None = None,
) -> ToolResult:
    """Create a consistent result for a JULI tool."""
    return {
        "tool": tool,
        "status": status,
        "data": data,
        "error": error,
    }

