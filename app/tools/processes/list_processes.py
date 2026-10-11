
import psutil


def list_processes() -> dict:
    """Return a read-only snapshot of running processes."""
    processes = []
    inaccessible_count = 0

    for process in psutil.process_iter(
        attrs=["pid", "name", "status", "memory_info"],
        ad_value=None,
    ):
        try:
            info = process.info
            memory_info = info.get("memory_info")

            processes.append(
                {
                    "pid": info["pid"],
                    "name": info["name"],
                    "status": info["status"],
                    "memory_rss_bytes": (
                        memory_info.rss if memory_info is not None else None
                    ),
                }
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            # The process may exit while it is being inspected.
            continue
        except psutil.AccessDenied:
            inaccessible_count += 1

    return {
        "tool": "list_processes",
        "status": "partial" if inaccessible_count else "success",
        "data": {
            "processes": processes,
            "process_count": len(processes),
            "inaccessible_count": inaccessible_count,
        },
        "error": (
            {
                "code": "ACCESS_DENIED",
                "message": "Some processes could not be inspected.",
            }
            if inaccessible_count
            else None
        ),
    }
