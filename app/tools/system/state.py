from app.tools.system.cpu import get_cpu_usage
from app.tools.system.memory import get_memory_usage
from app.tools.system.disk import get_disk_usage


def get_system_state() -> dict:
    """Return the current system state using system tools."""
    cpu = get_cpu_usage()
    memory = get_memory_usage()
    disk = get_disk_usage()

    return {
        "tool": "get_system_state",
        "status": "success",
        "data": {
            "cpu": cpu["data"],
            "memory": memory["data"],
            "disk": disk["data"],
        },
    }
