
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Observation:
    """Represent one timestamped observation of a system metric."""

    timestamp: str
    source: str
    metric: str
    value: float
    unit: str
    context: dict[str, Any] = field(default_factory=dict)
