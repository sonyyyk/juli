"""Deterministic, size-bounded monitoring context for the Agent Core.

``build_monitoring_context`` turns a ``run_monitoring_cycle()`` result into
short plain text for the language model. It is a pure function: it does not
modify its input and does not read the database, the clock, or the network.

Blocks, in output order:

1. Data freshness (never trimmed)
2. Latest stored values
3. Recent window: threshold anomalies and persistent series
4. Significance evidence for recent anomalies
5. PSI pressure, rendered once from ``pressure_observations``
6. Data quality issues, counted by type
7. Historical summary
8. What monitoring does not know (never trimmed)

When the text exceeds ``max_chars``, the oldest items are removed from block
7, then 6, then 4, then 3; after that PSI, the latest values, and finally the
emptied blocks 7, 6, 4, 3 are each collapsed to a single short line. Missing or non-finite values are rendered as
``unknown``, never as zero.
"""

from collections import Counter
from collections.abc import Mapping
import math
import re
from typing import Any, Callable

from app.config import get_monitoring_context_max_chars
from app.monitoring.data_quality import parse_snapshot_timestamp


MIN_CONTEXT_CHARS = 1000
MAX_RECENT_ITEMS = 10
MAX_TEXT_CHARS = 120
MAX_TIMESTAMP_CHARS = 35
MAX_LISTED_NAMES = 5

FRESHNESS_STATES = ("fresh", "stale", "no_data")
CURRENT_METRICS = (("cpu_usage", "CPU"), ("memory_usage", "RAM"), ("disk_usage", "Disk"))
PSI_RESOURCES = ("cpu", "memory", "io")
PSI_STALL_TYPES = ("some", "full")
PSI_WINDOWS = ("avg10", "avg60", "avg300")
_PSI_METRIC = re.compile(r"psi_(cpu|memory|io)_(some|full)_(avg10|avg60|avg300)")
_STATUS = re.compile(r"[a-z_]{1,20}")

UNKNOWN_STATE_WARNING = (
    "CURRENT SYSTEM STATE IS UNKNOWN; values below are the last stored "
    "measurements, not the present state."
)
FUTURE_WARNING = (
    "WARNING: the newest snapshot is timestamped in the future; the clock is unreliable."
)


def build_monitoring_context(result: object, max_chars: int | None = None) -> str:
    """Return a plain-text monitoring summary no longer than ``max_chars``.

    ``max_chars`` defaults to ``JULI_MONITORING_CONTEXT_MAX_CHARS`` and must
    be an integer of at least ``MIN_CONTEXT_CHARS``; otherwise ``ValueError``.
    """
    if max_chars is None:
        max_chars = get_monitoring_context_max_chars()
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if max_chars < MIN_CONTEXT_CHARS:
        raise ValueError(f"max_chars must be at least {MIN_CONTEXT_CHARS}")

    result = result if _is_record(result) else None
    recent = _get(result, "recent")
    assessments = _newest_first(
        _records(_get(recent, "significance_assessments")),
        lambda item: _get(_get(item, "anomaly"), "timestamp"),
    )

    freshness = _freshness_block(result)
    current = _current_block(result)
    recent_block = _recent_block(result, recent)
    significance = _significance_block(recent, assessments)
    psi = _psi_block(result)
    issues = _issues_block(result)
    historical = _historical_block(result)
    unknown = _unknown_block(result, assessments)

    blocks = [freshness, current, recent_block, significance, psi, issues, historical, unknown]
    trim_order = [historical, issues, significance, recent_block]
    _fit(blocks, max_chars, trim_order=trim_order, collapse_order=[psi, current, *trim_order])
    return "\n\n".join(block.text() for block in blocks)


class _Block:
    """A header, removable item lines, and an omission marker."""

    def __init__(
        self,
        header: str | None,
        items: list[str] | None = None,
        empty_text: str | None = "none",
        omitted: int = 0,
        collapsed: str | None = None,
    ) -> None:
        self.header = header
        self.items = list(items or [])
        self.empty_text = empty_text
        self.omitted = omitted
        self.collapsed = collapsed
        self._items_chars = sum(len(item) for item in self.items)

    def _tail(self) -> str | None:
        if self.omitted:
            noun = "item" if self.omitted == 1 else "items"
            return f"- ({self.omitted} {noun} omitted)"
        if not self.items and self.empty_text:
            return f"- {self.empty_text}"
        return None

    def length(self) -> int:
        parts = [len(self.header)] if self.header is not None else []
        parts.extend([0] * len(self.items))
        tail = self._tail()
        if tail is not None:
            parts.append(len(tail))
        return self._items_chars + sum(parts) + max(len(parts) - 1, 0)

    def text(self) -> str:
        lines = [self.header] if self.header is not None else []
        lines.extend(self.items)
        tail = self._tail()
        if tail is not None:
            lines.append(tail)
        return "\n".join(lines)

    def pop_last(self) -> None:
        self._items_chars -= len(self.items.pop())
        self.omitted += 1

    def collapse(self) -> None:
        if self.collapsed is None:
            return
        count = self.omitted + len(self.items)
        self.header = self.collapsed.format(omitted=f"{count} item" + ("" if count == 1 else "s"))
        self.items = []
        self._items_chars = 0
        self.empty_text = None
        self.omitted = 0


def _fit(
    blocks: list[_Block],
    max_chars: int,
    trim_order: list[_Block],
    collapse_order: list[_Block],
) -> None:
    separators = 2 * (len(blocks) - 1)
    total = sum(block.length() for block in blocks) + separators
    for block in trim_order:
        while total > max_chars and block.items:
            before = block.length()
            block.pop_last()
            total += block.length() - before
    for block in collapse_order:
        if total <= max_chars:
            break
        before = block.length()
        block.collapse()
        total += block.length() - before


# --- Blocks -----------------------------------------------------------------


def _freshness_block(result: object) -> _Block:
    if result is None:
        lines = [
            "Data freshness: unknown; the monitoring result is unavailable.",
            UNKNOWN_STATE_WARNING,
            "Monitoring status: unavailable.",
        ]
        return _Block(None, lines, empty_text=None)

    freshness = _get(result, "freshness")
    state = _get(freshness, "state")
    if state not in FRESHNESS_STATES:
        state = "unknown"
    timestamp = _get(freshness, "latest_snapshot_timestamp")
    age = _num(_get(freshness, "latest_snapshot_age_seconds"))
    limit = _num(_get(freshness, "stale_after_seconds"))

    details = []
    if not _is_record(freshness):
        details.append("freshness was not reported")
    elif state == "no_data":
        details.append("no stored snapshot with a valid timestamp")
    if timestamp is not None:
        details.append(f"latest snapshot {_ts(timestamp)}")
    if age is not None and age >= 0:
        details.append(f"age {_duration(age)}")
    elif age is not None:
        details.append(f"timestamped {_duration(age)} in the future")
    elif state in ("fresh", "stale"):
        details.append("age unknown")
    if limit is not None:
        details.append(f"freshness limit {_duration(limit)}")

    lines = [f"Data freshness: {state}" + ("; " + ", ".join(details) if details else "") + "."]
    if age is not None and age < 0:
        lines.append(FUTURE_WARNING)
    if state != "fresh":
        lines.append(UNKNOWN_STATE_WARNING)

    status = _get(result, "status")
    status = status if isinstance(status, str) and _STATUS.fullmatch(status) else "unknown"
    lines.append(
        f"Monitoring status: {status}; snapshots analyzed: "
        f"{_count(_get(result, 'snapshots_analyzed'))}."
    )
    return _Block(None, lines, empty_text=None)


def _current_block(result: object) -> _Block:
    current = _get(result, "current")
    if not _is_record(current):
        line = "Latest stored values: none available."
    else:
        values = ", ".join(
            f"{label} {_with_unit(_get(current, metric), '%')}"
            for metric, label in CURRENT_METRICS
        )
        line = f"Latest stored values ({_ts(_get(current, 'timestamp'))}): {values}."
    return _Block(None, [line], empty_text=None, collapsed="Latest stored values omitted.")


def _recent_block(result: object, recent: object) -> _Block:
    if not _is_record(recent):
        state = "unknown" if result is None else "not reported"
        return _Block(f"Recent window: {state}.", empty_text=None)

    minutes = _num(_get(recent, "window_minutes"))
    if minutes is not None and minutes.is_integer():
        minutes_text = _fmt(minutes, 0)
    else:
        minutes_text = _fmt(minutes)
    header = (
        f"Recent window (last {minutes_text} min, "
        f"since {_ts(_get(recent, 'window_start'))}):"
    )
    series = _newest_first(
        _records(_get(recent, "persistent_anomalies")), lambda item: _get(item, "end")
    )
    anomalies = _newest_first(
        _records(_get(recent, "threshold_anomalies")), lambda item: _get(item, "timestamp")
    )
    items = [_series_line(item) for item in series[:MAX_RECENT_ITEMS]]
    items += [_anomaly_line(item) for item in anomalies[:MAX_RECENT_ITEMS]]
    omitted = max(len(series) - MAX_RECENT_ITEMS, 0) + max(len(anomalies) - MAX_RECENT_ITEMS, 0)
    return _Block(
        header,
        items,
        empty_text="none (no threshold anomalies or persistent series in the window)",
        omitted=omitted,
        collapsed="Recent window: {omitted} omitted.",
    )


def _series_line(series: object) -> str:
    threshold = _with_unit(_get(series, "threshold"), "%")
    span = _num(_get(series, "observed_span_seconds"))
    return (
        f"- persistent series: {_text(_get(series, 'metric'))} > {threshold} "
        f"from {_ts(_get(series, 'start'))} to {_ts(_get(series, 'end'))}, "
        f"{_count(_get(series, 'samples'))} samples, "
        f"max {_with_unit(_get(series, 'max_value'), '%')}, "
        f"span {_duration(span) if span is not None and span >= 0 else 'unknown'}"
    )


def _anomaly_line(anomaly: object) -> str:
    unit = _unit(_get(anomaly, "unit"))
    return (
        f"- threshold: {_ts(_get(anomaly, 'timestamp'))} {_text(_get(anomaly, 'metric'))} "
        f"{_with_unit(_get(anomaly, 'value'), unit)} > "
        f"{_with_unit(_get(anomaly, 'threshold'), unit)}"
    )


def _significance_block(recent: object, assessments: list[object]) -> _Block:
    header = "Significance evidence for recent anomalies (no severity assigned):"
    if not _is_record(recent):
        return _Block(header, empty_text="not reported")
    if not assessments:
        return _Block(header)

    persistence_unknown, quality_unknown = _unknown_counts(assessments)
    summary = (
        f"- summary: {len(assessments)} assessment(s); persistence unknown for "
        f"{persistence_unknown}, data quality unknown for {quality_unknown}"
    )
    if persistence_unknown:
        summary += "; persistence is not confirmed for those anomalies"
    items = [summary] + [_assessment_line(item) for item in assessments]
    return _Block(header, items, collapsed="Significance evidence: {omitted} omitted.")


def _assessment_line(assessment: object) -> str:
    anomaly = _get(assessment, "anomaly")
    unit = _unit(_get(anomaly, "unit"))
    parts = []

    magnitude = _get(assessment, "magnitude")
    delta = _num(_get(magnitude, "value"))
    if _get(magnitude, "status") == "available" and delta is not None:
        parts.append(f"+{_with_unit(delta, _unit(_get(magnitude, 'unit')) or unit)} over threshold")
    else:
        parts.append("excess over threshold unknown")

    persistence = _get(assessment, "persistence")
    persistence_status = _get(persistence, "status")
    if persistence_status == "confirmed":
        parts.append("persistence confirmed")
    else:
        detail = _get(persistence, "detail")
        parts.append(
            f"persistence {_text(persistence_status) if persistence_status else 'unknown'}"
            + (f": {_text(detail)}" if detail else "")
        )

    quality = _get(assessment, "data_quality")
    quality_status = _get(quality, "status")
    if quality_status == "affected":
        parts.append(f"data quality affected ({len(_records(_get(quality, 'issues')))} issues)")
    elif quality_status == "clear":
        parts.append("data quality clear")
    else:
        parts.append("data quality unknown")

    corroborating = _get(assessment, "corroborating_evidence")
    same = [
        _get(item, "metric") for item in _records(_get(corroborating, "same_snapshot_anomalies"))
    ]
    if same:
        parts.append(f"same-snapshot anomalies: {_names(same)} (causality not established)")

    return (
        f"- {_ts(_get(anomaly, 'timestamp'))} {_text(_get(anomaly, 'metric'))} "
        f"{_with_unit(_get(anomaly, 'value'), unit)}: " + "; ".join(parts)
    )


def _psi_block(result: object) -> _Block:
    observations = _records(_get(result, "pressure_observations"))
    grid: dict[tuple[str, str, str], float | None] = {}
    sampled_at: list[object] = []
    other = 0
    for observation in observations:
        key = _psi_key(observation)
        if key is None:
            other += 1
            continue
        grid.setdefault(key, _num(_get(observation, "value")))
        sampled_at.append(_get(observation, "timestamp"))

    if not grid:
        if result is None:
            return _Block("PSI pressure: unknown.", empty_text=None)
        counts = _issue_counts(_get(result, "pressure_issues"))
        items = [f"- reason: {name} ({count})" for name, count in counts]
        if other:
            items.append(f"- {other} unrecognized pressure observation(s) not shown")
        return _Block(
            "PSI pressure: unavailable.",
            items,
            empty_text="no PSI observations or issues reported",
            collapsed="PSI omitted (unavailable).",
        )

    newest = _newest_first(sampled_at, lambda item: item)
    header = (
        f"PSI pressure (sampled at {_ts(newest[0] if newest else None)}, when this analysis ran; "
        "NOT temporally linked to the anomalies above):"
    )
    items = []
    for resource in PSI_RESOURCES:
        for stall in PSI_STALL_TYPES:
            values = " / ".join(
                f"{window} {_psi_value(grid.get((resource, stall, window)))}"
                for window in PSI_WINDOWS
            )
            items.append(f"- {resource} {stall}: {values} %")
    if other:
        items.append(f"- {other} unrecognized pressure observation(s) not shown")
    return _Block(header, items, collapsed=f"PSI omitted ({len(grid)} values).")


def _psi_key(observation: object) -> tuple[str, str, str] | None:
    context = _get(observation, "context")
    key = (_get(context, "resource"), _get(context, "stall_type"), _get(context, "window"))
    if key[0] in PSI_RESOURCES and key[1] in PSI_STALL_TYPES and key[2] in PSI_WINDOWS:
        return key  # type: ignore[return-value]
    metric = _get(observation, "metric")
    match = _PSI_METRIC.fullmatch(metric) if isinstance(metric, str) else None
    return match.groups() if match else None  # type: ignore[return-value]


def _psi_value(value: float | None) -> str:
    return "n/a" if value is None else _fmt(value, digits=2)


def _issues_block(result: object) -> _Block:
    header = "Data quality issues (counts by type):"
    if result is None:
        return _Block(header, empty_text="unknown")
    items = []
    for key, label in (
        ("data_issues", "data"),
        ("temporal_errors", "temporal"),
        ("pressure_issues", "PSI"),
    ):
        items += [f"- {label}: {name} ({count})" for name, count in _issue_counts(_get(result, key))]
    return _Block(header, items, collapsed="Data quality issues: {omitted} omitted.")


def _historical_block(result: object) -> _Block:
    historical = _get(result, "historical")
    if not _is_record(historical):
        state = "unknown" if result is None else "not reported"
        return _Block(f"Historical (outside the recent window): {state}.", empty_text=None)

    threshold = _count(_get(historical, "threshold_anomalies_found"))
    series = _count(_get(historical, "persistent_anomalies_found"))
    line = (
        f"- analyzed {_ts(_get(historical, 'analyzed_from'))} to "
        f"{_ts(_get(historical, 'analyzed_to'))}: {threshold} threshold anomalies, "
        f"{series} persistent series"
    )
    metrics = _get(historical, "metrics")
    if isinstance(metrics, (list, tuple)) and metrics:
        line += f", metrics: {_names(metrics)}"
    last = _get(historical, "last_anomaly_timestamp")
    if last is not None:
        line += f", last at {_ts(last)}"
    unplaced = [
        _num(_get(historical, "unplaced_threshold_anomalies")),
        _num(_get(historical, "unplaced_persistent_series")),
    ]
    if any(value for value in unplaced):
        line += (
            f", without a valid timestamp: {_count(unplaced[0])} threshold anomalies, "
            f"{_count(unplaced[1])} series"
        )
    return _Block(
        "Historical (outside the recent window):",
        [line],
        collapsed="Historical: {omitted} omitted.",
    )


def _unknown_block(result: object, assessments: list[object]) -> _Block:
    items = []
    if result is None:
        items.append("- The monitoring result is unavailable; the whole system state is unknown.")
    else:
        state = _get(_get(result, "freshness"), "state")
        if state != "fresh":
            state = state if state in FRESHNESS_STATES else "unknown"
            items.append(f"- The current system state (data freshness: {state}).")
        current = _get(result, "current")
        if _is_record(current):
            missing = [label for metric, label in CURRENT_METRICS if _num(_get(current, metric)) is None]
            if missing:
                items.append(f"- Latest stored values for: {', '.join(missing)}.")
        else:
            items.append("- Latest stored values (none available).")
        persistence_unknown, quality_unknown = _unknown_counts(assessments)
        parts = []
        if persistence_unknown:
            parts.append(f"persistence of {persistence_unknown}")
        if quality_unknown:
            parts.append(f"data quality of {quality_unknown}")
        if parts:
            items.append(f"- Recent anomalies ({len(assessments)}): {', '.join(parts)}.")
    items += [
        "- Affected services and users (not collected).",
        "- Time link between PSI and the anomalies (not assessed).",
        "- Causes of the anomalies (monitoring does not establish causality).",
    ]
    return _Block("Unknown or not established by monitoring:", items, empty_text=None)


# --- Helpers ----------------------------------------------------------------


def _is_record(value: object) -> bool:
    if value is None or isinstance(value, (str, bytes, bytearray, int, float, list, tuple, set)):
        return False
    return True


def _get(record: object, key: str) -> Any:
    if isinstance(record, Mapping):
        try:
            return record.get(key)
        except Exception:
            return None
    if not _is_record(record):
        return None
    try:
        value = getattr(record, key, None)
    except Exception:
        return None
    return None if callable(value) else value


def _records(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if _is_record(item)]


def _newest_first(items: list[object], timestamp_of: Callable[[object], object]) -> list[object]:
    """Sort by parsed timestamp, newest first; unparseable items keep input order last."""
    keyed = []
    for index, item in enumerate(items):
        moment = parse_snapshot_timestamp(timestamp_of(item))
        keyed.append(((0, -moment.timestamp(), index) if moment else (1, 0.0, index), item))
    keyed.sort(key=lambda pair: pair[0])
    return [item for _, item in keyed]


def _unknown_counts(assessments: list[object]) -> tuple[int, int]:
    persistence = sum(
        1 for item in assessments if _get(_get(item, "persistence"), "status") != "confirmed"
    )
    quality = sum(
        1
        for item in assessments
        if _get(_get(item, "data_quality"), "status") not in ("clear", "affected")
    )
    return persistence, quality


def _issue_counts(issues: object) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for issue in _records(issues):
        name = _get(issue, "issue")
        counter[_text(name, 60) if name is not None else "unspecified"] += 1
    return sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "unknown"
    return f"{value:.{digits}f}" if abs(value) < 1e6 else f"{value:.3g}"


def _count(value: object) -> str:
    number = _num(value)
    if number is None or number < 0 or number != int(number):
        return "unknown"
    return str(int(number)) if number < 1e9 else f"{number:.3g}"


def _unit(value: object) -> str:
    return _text(value, 10) if isinstance(value, str) else ""


def _with_unit(value: object, unit: str) -> str:
    number = _num(value)
    if number is None:
        return "unknown"
    return f"{_fmt(number)} {unit}".rstrip()


def _duration(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < 120:
        return f"{_fmt(seconds, 0)} s"
    if seconds < 7200:
        return f"{_fmt(seconds / 60)} min"
    if seconds < 172800:
        return f"{_fmt(seconds / 3600)} h"
    return f"{_fmt(seconds / 86400)} days"


def _text(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        text = value
    else:
        try:
            text = str(value)
        except Exception:
            return "unknown"
    text = " ".join(text.split())
    if not text:
        return "unknown"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _ts(value: object) -> str:
    return _text(value, MAX_TIMESTAMP_CHARS)


def _names(values: object) -> str:
    names = [_text(value, 40) for value in values] if isinstance(values, (list, tuple)) else []
    shown = ", ".join(names[:MAX_LISTED_NAMES])
    extra = len(names) - MAX_LISTED_NAMES
    return shown + (f" (+{extra} more)" if extra > 0 else "")
