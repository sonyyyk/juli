import time
import json
import os
import platform
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil


# JULI Dataset Collection Kit v1
# Read-only process data collection.
# Does not execute shell commands or modify system processes.

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
MAX_PROCESSES = 500



def collect_processes():
    """Collect process information with a short CPU sampling interval."""
    processes = []
    inaccessible = 0
    candidates = []

    # First pass: collect basic information and prime CPU counters.
    for proc in psutil.process_iter(
        attrs=["pid", "name", "status", "memory_info"]
    ):
        if len(candidates) >= MAX_PROCESSES:
            break

        try:
            proc.cpu_percent(interval=None)
            candidates.append(proc)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            inaccessible += 1

    # Allow CPU counters to measure a real interval.
    time.sleep(0.5)

    # Second pass: collect CPU measurements and process information.
    for proc in candidates:
        try:
            cpu_percent = proc.cpu_percent(interval=None)
            info = proc.as_dict(
                attrs=["pid", "name", "status", "memory_info"]
            )
            memory_info = info.get("memory_info")

            processes.append({
                "pid": info.get("pid"),
                "name": info.get("name"),
                "status": info.get("status"),
                "cpu_percent": cpu_percent,
                "memory_rss_bytes": (
                    memory_info.rss if memory_info else None
                ),
            })

        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            inaccessible += 1

    return processes, inaccessible


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    processes, inaccessible = collect_processes()

    bundle = {
        "schema_version": "1.0",
        "bundle_id": str(uuid.uuid4()),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "collector": {
            "name": "JULI Dataset Collection Kit",
            "version": "1.0",
            "category": "Processes",
            "mode": "read_only",
        },
        "system": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        "collection": {
            "process_count": len(processes),
            "inaccessible_count": inaccessible,
            "truncated": len(processes) >= MAX_PROCESSES,
        },
        "data": {
            "processes": processes,
        },
    }

    filename = (
        "processes_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".json"
    )
    output_path = OUTPUT_DIR / filename

    with output_path.open("x", encoding="utf-8") as file:
        json.dump(bundle, file, indent=2, ensure_ascii=False)

    print("JULI Processes Collector completed.")
    print(f"Processes collected: {len(processes)}")
    print(f"Inaccessible processes: {inaccessible}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
