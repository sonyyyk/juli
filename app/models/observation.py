
from dataclasses import dataclass, field
import math
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

    def __post_init__(self) -> None:
        """Reject values that cannot represent a finite numeric measurement."""
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("observation value must be a finite number")
        try:
            finite = math.isfinite(self.value)
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError("observation value must be a finite number")
