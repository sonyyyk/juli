
from pathlib import Path
import math

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
    malformed: dict[str, list[str]] = {}

    for resource, path in PSI_PATHS.items():
        try:
            content = path.read_text(encoding="utf-8")
            resource_data: dict[str, dict[str, float | int]] = {}

            for line in content.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] not in {"some", "full"}:
                    malformed.setdefault(resource, []).append(
                        f"Unrecognized PSI line: {line}"
                    )
                    continue

                metrics: dict[str, float | int] = {}

                for item in parts[1:]:
                    key, separator, raw_value = item.partition("=")
                    if not separator:
                        malformed.setdefault(resource, []).append(
                            f"Malformed PSI field: {item}"
                        )
                        continue

                    if key == "total":
                        try:
                            metrics[key] = int(raw_value)
                        except ValueError:
                            malformed.setdefault(resource, []).append(
                                f"Invalid PSI total value: {raw_value}"
                            )
                    elif key in {"avg10", "avg60", "avg300"}:
                        try:
                            value = float(raw_value)
                            if not math.isfinite(value) or not 0 <= value <= 100:
                                raise ValueError("value outside 0..100")
                            metrics[key] = value
                        except ValueError:
                            malformed.setdefault(resource, []).append(
                                f"Invalid PSI {key} value: {raw_value}"
                            )

                resource_data[parts[0]] = metrics

            if resource_data:
                data[resource] = resource_data
            else:
                unavailable[resource] = "PSI data is empty or unrecognized"

        except (OSError, ValueError) as error:
            unavailable[resource] = str(error)

    if not data and not malformed:
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
        "malformed": malformed,
    }

    return make_tool_result(
        tool="get_system_pressure",
        status="partial" if unavailable or malformed else "success",
        data=result_data,
        error=(
            {
                "code": "PSI_PARTIALLY_UNAVAILABLE",
                "message": "Some PSI resources could not be read.",
            }
            if unavailable or malformed
            else None
        ),
    )
