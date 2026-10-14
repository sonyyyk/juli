
from typing import Any, Callable

from app.tools.processes.get_process_info import get_process_info
from app.tools.processes.list_processes import list_processes
from app.tools.system.cpu import get_cpu_usage
from app.tools.system.disk import get_disk_usage
from app.tools.system.memory import get_memory_usage
from app.tools.system.pressure import get_system_pressure
from app.tools.system.state import get_system_state


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "get_cpu_usage": get_cpu_usage,
    "get_memory_usage": get_memory_usage,
    "get_disk_usage": get_disk_usage,
    "get_system_state": get_system_state,
    "get_system_pressure": get_system_pressure,
    "list_processes": list_processes,
    "get_process_info": get_process_info,
}


TOOL_METADATA: dict[str, dict[str, Any]] = {
    "get_cpu_usage": {
        "category": "system",
        "description": "Read current CPU usage.",
        "operation": "read",
        "parameters": {},
    },
    "get_memory_usage": {
        "category": "system",
        "description": "Read current memory usage.",
        "operation": "read",
        "parameters": {},
    },
    "get_disk_usage": {
        "category": "system",
        "description": "Read disk usage for the root filesystem.",
        "operation": "read",
        "parameters": {},
    },
    "get_system_state": {
        "category": "system",
        "description": "Collect CPU, memory, and disk usage.",
        "operation": "read",
        "parameters": {},
    },
    "get_system_pressure": {
        "category": "system",
        "description": "Read Linux CPU, memory, and I/O pressure metrics.",
        "operation": "read",
        "parameters": {},
    },
    "list_processes": {
        "category": "processes",
        "description": "List accessible system processes.",
        "operation": "read",
        "parameters": {},
    },
    "get_process_info": {
        "category": "processes",
        "description": "Read details about a process by PID.",
        "operation": "read",
        "parameters": {
            "pid": {
                "type": "integer",
                "required": True,
                "description": "Process ID.",
            },
        },
    },
}


def get_tool(name: str) -> Callable[..., dict[str, Any]] | None:
    """Return a registered tool by name."""
    return TOOL_REGISTRY.get(name)


def list_registered_tools() -> list[str]:
    """Return the names of all registered tools."""
    return sorted(TOOL_REGISTRY)


def get_tool_metadata(name: str) -> dict[str, Any] | None:
    """Return metadata for a registered tool."""
    if name not in TOOL_REGISTRY:
        return None

    return TOOL_METADATA.get(name)
