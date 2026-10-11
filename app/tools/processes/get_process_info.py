
import psutil


def get_process_info(pid: int) -> dict:
    """Return read-only information about a process by PID."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return {
            "tool": "get_process_info",
            "status": "error",
            "data": None,
            "error": {
                "code": "INVALID_PID",
                "message": "PID must be a positive integer.",
            },
        }

    try:
        process = psutil.Process(pid)

        with process.oneshot():
            memory_info = process.memory_info()

            data = {
                "pid": process.pid,
                "name": process.name(),
                "status": process.status(),
                "cpu_percent": process.cpu_percent(interval=None),
                "memory_rss_bytes": memory_info.rss,
                "memory_vms_bytes": memory_info.vms,
                "create_time": process.create_time(),
            }

        return {
            "tool": "get_process_info",
            "status": "success",
            "data": data,
            "error": None,
        }

    except psutil.NoSuchProcess:
        return {
            "tool": "get_process_info",
            "status": "error",
            "data": None,
            "error": {
                "code": "PROCESS_NOT_FOUND",
                "message": f"Process with PID {pid} does not exist.",
            },
        }

    except psutil.AccessDenied:
        return {
            "tool": "get_process_info",
            "status": "error",
            "data": None,
            "error": {
                "code": "ACCESS_DENIED",
                "message": f"Access denied for process with PID {pid}.",
            },
        }

    except psutil.ZombieProcess:
        return {
            "tool": "get_process_info",
            "status": "partial",
            "data": {"pid": pid},
            "error": {
                "code": "ZOMBIE_PROCESS",
                "message": "The process is a zombie; detailed information may be unavailable.",
            },
        }
