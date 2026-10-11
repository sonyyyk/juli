
import json
import sys
from pathlib import Path

SENSITIVE_SYSTEM_FIELDS = {"os_release", "python_version"}


def sanitize_bundle(source: Path, destination: Path) -> None:
    bundle = json.loads(source.read_text(encoding="utf-8"))

    # Remove detailed system fingerprinting information.
    system = bundle.get("system", {})
    for field in SENSITIVE_SYSTEM_FIELDS:
        system.pop(field, None)

    # Remove original PIDs and process names.
    processes = bundle.get("data", {}).get("processes", [])
    for index, proc in enumerate(processes, start=1):
        proc.pop("pid", None)
        proc.pop("name", None)
        proc["process_id"] = f"process_{index:03d}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as file:
        json.dump(bundle, file, indent=2, ensure_ascii=False)

    print(f"Sanitized bundle created: {destination}")
    print(f"Processes retained: {len(processes)}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python sanitize_bundle.py <source.json>")
        raise SystemExit(2)

    source = Path(sys.argv[1])
    if not source.is_file():
        print(f"ERROR: Source file not found: {source}")
        raise SystemExit(2)

    destination = (
        Path("output/sanitized") / f"{source.stem}_sanitized.json"
    )

    sanitize_bundle(source, destination)


if __name__ == "__main__":
    main()
