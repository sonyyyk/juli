from typing import Any, Literal, NotRequired, TypedDict, get_args


ToolStatus = Literal["success", "partial", "error"]

_VALID_STATUSES = frozenset(get_args(ToolStatus))


class ToolError(TypedDict):
    code: str
    message: str
    details: NotRequired[dict[str, Any]]


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
    """Create a consistent result for a JULI tool.

    An ``error`` status always carries ``data=None``. An unknown status, or a
    ``success`` status that carries an error, is a programming mistake and
    raises ``ValueError`` instead of being silently corrected.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Unknown tool status: {status!r}")
    if status == "success" and error is not None:
        raise ValueError("A successful tool result must not carry an error.")
    if status == "error":
        data = None

    return {
        "tool": tool,
        "status": status,
        "data": data,
        "error": error,
    }
