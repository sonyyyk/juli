
import json
import sys
from datetime import datetime
from pathlib import Path

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "bundle_id",
    "collected_at",
    "collector",
    "system",
    "collection",
    "data",
}

RAW_FIELDS = {
    "pid", "name", "status", "cpu_percent", "memory_rss_bytes"
}

SANITIZED_FIELDS = {
    "process_id", "status", "cpu_percent", "memory_rss_bytes"
}


def validate_bundle(path: Path) -> bool:
    errors = []
    warnings = []

    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Cannot read JSON: {exc}")
        return False

    if not isinstance(bundle, dict):
        print("ERROR: Bundle must be a JSON object.")
        return False

    missing = REQUIRED_TOP_LEVEL - bundle.keys()
    if missing:
        print(f"ERROR: Missing fields: {sorted(missing)}")
        return False

    if bundle["schema_version"] != "1.0":
        errors.append("Unsupported schema version.")

    try:
        timestamp = datetime.fromisoformat(
            bundle["collected_at"].replace("Z", "+00:00")
        )
        if timestamp.tzinfo is None:
            errors.append("Timestamp must include a timezone.")
    except (TypeError, ValueError, AttributeError):
        errors.append("Invalid collected_at timestamp.")

    collector = bundle["collector"]
    collection = bundle["collection"]
    data = bundle["data"]

    if not isinstance(collector, dict):
        errors.append("collector must be an object.")
        collector = {}

    if collector.get("category") != "Processes":
        errors.append("Unexpected collector category.")

    if collector.get("mode") != "read_only":
        errors.append("Collection mode must be read_only.")

    if not isinstance(collection, dict):
        errors.append("collection must be an object.")
        collection = {}

    if not isinstance(data, dict):
        errors.append("data must be an object.")
        data = {}

    processes = data.get("processes")
    if not isinstance(processes, list):
        errors.append("data.processes must be a list.")
        processes = []

    if collection.get("process_count") != len(processes):
        errors.append("Process count does not match records.")

    sanitized = any(
        isinstance(proc, dict) and "process_id" in proc
        for proc in processes
    )

    allowed_fields = SANITIZED_FIELDS if sanitized else RAW_FIELDS
    seen_ids = set()

    for index, proc in enumerate(processes):
        if not isinstance(proc, dict):
            errors.append(f"Record {index}: must be an object.")
            continue

        unknown = set(proc) - allowed_fields
        if unknown:
            errors.append(
                f"Record {index}: unexpected fields {sorted(unknown)}"
            )

        if sanitized:
            expected_id = f"process_{index + 1:03d}"
            if proc.get("process_id") != expected_id:
                errors.append(
                    f"Record {index}: invalid process_id."
                )
        else:
            pid = proc.get("pid")
            if (
                not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid < 0
            ):
                errors.append(f"Record {index}: invalid PID.")
            elif pid in seen_ids:
                errors.append(f"Record {index}: duplicate PID.")
            else:
                seen_ids.add(pid)

            name = proc.get("name")
            if name is not None and not isinstance(name, str):
                errors.append(f"Record {index}: invalid process name.")

        cpu = proc.get("cpu_percent")
        if cpu is not None and (
            not isinstance(cpu, (int, float))
            or isinstance(cpu, bool)
            or cpu < 0
        ):
            errors.append(f"Record {index}: invalid CPU percentage.")

        memory = proc.get("memory_rss_bytes")
        if memory is not None and (
            not isinstance(memory, int)
            or isinstance(memory, bool)
            or memory < 0
        ):
            errors.append(f"Record {index}: invalid RSS memory.")

    if sanitized:
        if any(
            isinstance(proc, dict)
            and ("pid" in proc or "name" in proc)
            for proc in processes
        ):
            errors.append("Sanitized data contains PID or process name.")

        system = bundle["system"]
        if isinstance(system, dict) and (
            "os_release" in system or "python_version" in system
        ):
            errors.append("Sanitized metadata contains restricted fields.")

        print("Format: sanitized")
    else:
        print("Format: raw")
        warnings.append("Review privacy before sharing this bundle.")

    if collection.get("truncated") is True:
        warnings.append("Collection reached its process limit.")

    print(f"File: {path}")
    print(f"Process records: {len(processes)}")

    if errors:
        print("\nVALIDATION: FAILED")
        for error in errors:
            print(f"  ERROR: {error}")
    else:
        print("\nVALIDATION: PASSED")

    for warning in warnings:
        print(f"  WARNING: {warning}")

    return not errors


def main():
    if len(sys.argv) != 2:
        print("Usage: python validate_bundle.py <bundle.json>")
        raise SystemExit(2)

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: File not found: {path}")
        raise SystemExit(2)

    raise SystemExit(0 if validate_bundle(path) else 1)


if __name__ == "__main__":
    main()
