
from pathlib import Path

from app.tools.result import make_tool_result


PSI_PATHS = {
    "cpu": Path("/proc/pressure/cpu"),
    "memory": Path("/proc/pressure/memory"),
    "io": Path("/proc/pressure/io"),
}


def get_system_pressure() -> dict:
    """Read Linux PSI metrics without modifying system state."""
    data: dict[str, dict[str, dict[str, float | int]]] = {}
    unavailable: dict[str, str] = {}

    for resource, path in PSI_PATHS.items():
        try:
            content = path.read_text(encoding="utf-8")
            resource_data: dict[str, dict[str, float | int]] = {}

            for line in content.splitlines():
                parts = line.split()
                if not parts or parts[0] not in {"some", "full"}:
                    continue

                metrics: dict[str, float | int] = {}

                for item in parts[1:]:
                    key, separator, raw_value = item.partition("=")
                    if not separator:
                        continue

                    if key == "total":
                        metrics[key] = int(raw_value)
                    elif key in {"avg10", "avg60", "avg300"}:
                        metrics[key] = float(raw_value)

                resource_data[parts[0]] = metrics

            if resource_data:
                data[resource] = resource_data
            else:
                unavailable[resource] = "PSI data is empty or unrecognized"

        except (OSError, ValueError) as error:
            unavailable[resource] = str(error)

    if not data:
        return make_tool_result(
            tool="get_system_pressure",
            status="error",
            data=None,
            error={
                "code": "PSI_UNAVAILABLE",
                "message": "No PSI resource data could be read.",
            },
        )

    result_data = {
        "resources": data,
        "unavailable": unavailable,
    }

    return make_tool_result(
        tool="get_system_pressure",
        status="partial" if unavailable else "success",
        data=result_data,
        error=(
            {
                "code": "PSI_PARTIALLY_UNAVAILABLE",
                "message": "Some PSI resources could not be read.",
            }
            if unavailable
            else None
        ),
    )
