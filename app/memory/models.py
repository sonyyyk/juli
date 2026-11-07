"""Dataclasses for the Memory Data Model v1 (docs/MEMORY_DATA_MODEL_v1.md).

Each class mirrors one table from section 2 and validates its own fields:
allowed values, ISO 8601 UTC timestamps, finite numbers, JSON-serializable
structures, and English-only ``statement`` fields (section 9, C12). The
classes hold data only; status rules live in ``transitions`` and the
repository.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
from typing import Any

from app.memory.ids import has_prefix


ACTORS = ("rule", "llm", "user")

INCIDENT_STATUSES = ("open", "investigating", "resolved", "unresolved")
INCIDENT_OPENED_BY = ("user", "juli_suggested")
OBSERVATION_KINDS = ("symptom", "metric", "state", "log", "user_report")
OBSERVATION_FRESHNESS = ("fresh", "stale", "unknown")
OBSERVATION_SOURCES = ("snapshot", "tool_result", "psi", "user")
HYPOTHESIS_ORIGINS = ("llm", "memory", "user")
HYPOTHESIS_STATUSES = ("proposed", "testing", "supported", "refuted", "inconclusive")
ACTION_KINDS = ("read_check", "action")
ACTION_INITIATORS = ("llm", "user", "memory_reverification")
ACTION_APPROVALS = ("not_required", "pending", "approved", "rejected")
ACTION_STATUSES = ("planned", "running", "succeeded", "failed", "skipped")
TOOL_RESULT_STATUSES = ("success", "partial", "error")
EVIDENCE_SOURCES = ("tool_result", "observation", "user")
EVIDENCE_QUALITY = ("fresh", "stale", "partial")
RELATIONS = (
    "supports", "contradicts", "causes", "verified_by", "resolved_by",
    "depends_on", "derived_from", "similar_to",
)
RELATION_CLASSES = ("associative", "semantic", "evidential", "causal")
# C15: what evidence supports or contradicts is an evidential relation.
EVIDENTIAL_RELATIONS = ("supports", "contradicts")
RELATION_STATUSES = ("asserted", "verified", "refuted")
KNOWLEDGE_KINDS = (
    "root_cause_pattern", "procedure", "dependency", "failed_attempt", "unverified_hypothesis",
)
KNOWLEDGE_STATUSES = ("verified", "failed", "unverified", "deprecated")
EVENT_TYPES = ("created", "status_changed", "linked", "consolidated", "reopened")
ENTITY_TYPES = (
    "incident", "observation", "hypothesis", "diagnostic_action",
    "tool_result", "evidence", "memory_relation", "knowledge",
)

_CYRILLIC = re.compile(r"[Ѐ-ԯⷠ-ⷿꙀ-ꚟ]")


def utc_now() -> str:
    """Return the current time as ISO 8601 UTC with microseconds."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_utc(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp with a zero UTC offset; return None otherwise."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset().total_seconds() != 0:
        return None
    return moment


# --- field validators ----------------------------------------------------------


def _choice(name: str, value: object, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}; got {value!r}")


def _text(name: str, value: object, required: bool = True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _english(name: str, value: object) -> None:
    _text(name, value)
    if _CYRILLIC.search(value):  # type: ignore[arg-type]
        raise ValueError(f"{name} must be written in English (Cyrillic text found)")


def _timestamp(name: str, value: object, required: bool = True) -> None:
    if value is None and not required:
        return
    if parse_utc(value) is None:
        raise ValueError(f"{name} must be an ISO 8601 timestamp in UTC; got {value!r}")


def _id(name: str, entity_type: str, value: object, required: bool = True) -> None:
    if value is None and not required:
        return
    if not has_prefix(entity_type, value):
        raise ValueError(f"{name} must be a {entity_type} id; got {value!r}")


def _json(name: str, value: object, kind: type | tuple[type, ...] = (dict, list)) -> None:
    if not isinstance(value, kind):
        raise ValueError(f"{name} must be a JSON {kind}")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable: {exc}") from None


def _finite(name: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _count(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


# --- row conversion ------------------------------------------------------------


class _Record:
    """Shared conversion between dataclasses and SQLite rows."""

    JSON_FIELDS: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: sqlite3.Row):
        values = {}
        for item in fields(cls):  # type: ignore[arg-type]
            value = row[item.name]
            if item.name in cls.JSON_FIELDS and value is not None:
                value = json.loads(value)
            values[item.name] = value
        return cls(**values)

    def to_row(self) -> dict[str, Any]:
        values = {}
        for item in fields(self):  # type: ignore[arg-type]
            value = getattr(self, item.name)
            if item.name in self.JSON_FIELDS and value is not None:
                value = dump_json(value)
            values[item.name] = value
        return values


def dump_json(value: object) -> str:
    """Serialize deterministically, keeping non-ASCII text readable."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


# --- entities (section 2) --------------------------------------------------------


@dataclass(frozen=True)
class Incident(_Record):
    id: str
    title: str
    symptom_text: str
    status: str
    opened_by: str
    host_context: dict[str, Any]
    created_at: str
    updated_at: str
    root_cause_hypothesis_id: str | None = None
    resolution_summary: str | None = None
    closed_at: str | None = None

    JSON_FIELDS = ("host_context",)

    def __post_init__(self) -> None:
        _id("id", "incident", self.id)
        _text("title", self.title)
        _text("symptom_text", self.symptom_text)  # the user's own words, any language
        _choice("status", self.status, INCIDENT_STATUSES)
        _choice("opened_by", self.opened_by, INCIDENT_OPENED_BY)
        _json("host_context", self.host_context, dict)
        _id("root_cause_hypothesis_id", "hypothesis", self.root_cause_hypothesis_id, False)
        _text("resolution_summary", self.resolution_summary, required=False)
        _timestamp("created_at", self.created_at)
        _timestamp("updated_at", self.updated_at)
        _timestamp("closed_at", self.closed_at, required=False)


@dataclass(frozen=True)
class IncidentObservation(_Record):
    """A memory observation (table ``observations``); not ``app.models.Observation`` (C2)."""

    id: str
    incident_id: str
    kind: str
    statement: str
    observed_at: str
    freshness: str
    source_type: str
    source_ref: str | None = None
    resource: str | None = None
    metric: str | None = None
    value: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        _id("id", "observation", self.id)
        _id("incident_id", "incident", self.incident_id)
        _choice("kind", self.kind, OBSERVATION_KINDS)
        _english("statement", self.statement)
        _timestamp("observed_at", self.observed_at)
        _choice("freshness", self.freshness, OBSERVATION_FRESHNESS)
        _choice("source_type", self.source_type, OBSERVATION_SOURCES)
        _text("source_ref", self.source_ref, required=False)
        if self.source_type in ("snapshot", "tool_result") and self.source_ref is None:
            raise ValueError(f"source_ref is required for source_type {self.source_type!r}")
        for name in ("resource", "metric", "unit"):
            _text(name, getattr(self, name), required=False)
        _finite("value", self.value)


@dataclass(frozen=True)
class Hypothesis(_Record):
    id: str
    incident_id: str
    statement: str
    origin: str
    status: str
    created_at: str
    updated_at: str
    origin_knowledge_id: str | None = None
    status_reason: str | None = None

    def __post_init__(self) -> None:
        _id("id", "hypothesis", self.id)
        _id("incident_id", "incident", self.incident_id)
        _english("statement", self.statement)
        _choice("origin", self.origin, HYPOTHESIS_ORIGINS)
        _choice("status", self.status, HYPOTHESIS_STATUSES)
        _id("origin_knowledge_id", "knowledge", self.origin_knowledge_id, False)
        if (self.origin == "memory") != (self.origin_knowledge_id is not None):
            raise ValueError("origin_knowledge_id is required for origin 'memory' and only for it")
        _text("status_reason", self.status_reason, required=False)
        _timestamp("created_at", self.created_at)
        _timestamp("updated_at", self.updated_at)


@dataclass(frozen=True)
class DiagnosticAction(_Record):
    id: str
    incident_id: str
    kind: str
    tool_name: str
    parameters: dict[str, Any]
    initiator: str
    approval: str
    status: str
    hypothesis_id: str | None = None
    risk_level: str | None = None
    tool_result_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    JSON_FIELDS = ("parameters",)

    def __post_init__(self) -> None:
        _id("id", "diagnostic_action", self.id)
        _id("incident_id", "incident", self.incident_id)
        _choice("kind", self.kind, ACTION_KINDS)
        _text("tool_name", self.tool_name)
        _json("parameters", self.parameters, dict)
        _choice("initiator", self.initiator, ACTION_INITIATORS)
        _choice("approval", self.approval, ACTION_APPROVALS)
        _choice("status", self.status, ACTION_STATUSES)
        _id("hypothesis_id", "hypothesis", self.hypothesis_id, False)
        _text("risk_level", self.risk_level, required=False)  # reserved for the Risk Analyzer
        _id("tool_result_id", "tool_result", self.tool_result_id, False)
        _timestamp("started_at", self.started_at, required=False)
        _timestamp("finished_at", self.finished_at, required=False)


@dataclass(frozen=True)
class StoredToolResult(_Record):
    """A persisted ToolResult (D4): ``payload`` = ``data``, ``error`` = ``error`` (C1)."""

    id: str
    tool_name: str
    status: str
    parameters: dict[str, Any]
    created_at: str
    payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    JSON_FIELDS = ("parameters", "payload", "error")

    def __post_init__(self) -> None:
        _id("id", "tool_result", self.id)
        _text("tool_name", self.tool_name)
        _choice("status", self.status, TOOL_RESULT_STATUSES)
        _json("parameters", self.parameters, dict)
        if self.payload is not None:
            _json("payload", self.payload, dict)
        if self.error is not None:
            _json("error", self.error, dict)
        if self.status == "error" and self.payload is not None:
            raise ValueError("an error tool result carries no payload")
        if self.status == "success" and self.error is not None:
            raise ValueError("a successful tool result carries no error")
        _timestamp("created_at", self.created_at)


@dataclass(frozen=True)
class Evidence(_Record):
    id: str
    incident_id: str
    summary: str
    source_type: str
    quality: str
    assessed_by: str
    created_at: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        _id("id", "evidence", self.id)
        _id("incident_id", "incident", self.incident_id)
        _text("summary", self.summary)
        _choice("source_type", self.source_type, EVIDENCE_SOURCES)
        if self.source_type == "tool_result":
            _id("source_ref", "tool_result", self.source_ref)
        elif self.source_type == "observation":
            _id("source_ref", "observation", self.source_ref)
        elif self.source_ref is not None:
            raise ValueError("user evidence has no source_ref")
        _choice("quality", self.quality, EVIDENCE_QUALITY)
        _choice("assessed_by", self.assessed_by, ACTORS)
        _timestamp("created_at", self.created_at)


@dataclass(frozen=True)
class MemoryRelation(_Record):
    id: str
    src_type: str
    src_id: str
    relation: str
    relation_class: str
    dst_type: str
    dst_id: str
    status: str
    created_by: str
    created_at: str
    basis_evidence_id: str | None = None

    def __post_init__(self) -> None:
        _id("id", "memory_relation", self.id)
        for side in ("src", "dst"):
            entity_type = getattr(self, f"{side}_type")
            _choice(f"{side}_type", entity_type, ENTITY_TYPES)
            _id(f"{side}_id", entity_type, getattr(self, f"{side}_id"))
        _choice("relation", self.relation, RELATIONS)
        _choice("relation_class", self.relation_class, RELATION_CLASSES)
        _choice("status", self.status, RELATION_STATUSES)
        _choice("created_by", self.created_by, ACTORS)
        _id("basis_evidence_id", "evidence", self.basis_evidence_id, False)
        _timestamp("created_at", self.created_at)


@dataclass(frozen=True)
class Knowledge(_Record):
    id: str
    kind: str
    statement: str
    status: str
    created_at: str
    updated_at: str
    symptom_signature: dict[str, Any] = field(default_factory=dict)
    verification_procedure: list[Any] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    source_incident_ids: list[str] = field(default_factory=list)
    times_confirmed: int = 0
    times_refuted: int = 0
    last_confirmed_at: str | None = None
    superseded_by: str | None = None

    JSON_FIELDS = ("symptom_signature", "verification_procedure", "scope", "source_incident_ids")

    def __post_init__(self) -> None:
        _id("id", "knowledge", self.id)
        _choice("kind", self.kind, KNOWLEDGE_KINDS)
        _english("statement", self.statement)
        _choice("status", self.status, KNOWLEDGE_STATUSES)
        _json("symptom_signature", self.symptom_signature, dict)
        _json("verification_procedure", self.verification_procedure, list)
        _json("scope", self.scope, dict)
        _json("source_incident_ids", self.source_incident_ids, list)
        for incident_id in self.source_incident_ids:
            _id("source_incident_ids item", "incident", incident_id)
        _count("times_confirmed", self.times_confirmed)
        _count("times_refuted", self.times_refuted)
        _timestamp("last_confirmed_at", self.last_confirmed_at, required=False)
        _id("superseded_by", "knowledge", self.superseded_by, False)
        if self.superseded_by == self.id:
            raise ValueError("knowledge cannot supersede itself")
        _timestamp("created_at", self.created_at)
        _timestamp("updated_at", self.updated_at)


@dataclass(frozen=True)
class MemoryEvent(_Record):
    id: str
    entity_type: str
    entity_id: str
    event: str
    actor: str
    at: str
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _id("id", "memory_event", self.id)
        _choice("entity_type", self.entity_type, ENTITY_TYPES)
        _id("entity_id", self.entity_type, self.entity_id)
        _choice("event", self.event, EVENT_TYPES)
        _choice("actor", self.actor, ACTORS)
        _text("reason", self.reason, required=False)
        _timestamp("at", self.at)


TABLES: dict[str, tuple[str, type[_Record]]] = {
    "incident": ("incidents", Incident),
    "observation": ("observations", IncidentObservation),
    "hypothesis": ("hypotheses", Hypothesis),
    "diagnostic_action": ("diagnostic_actions", DiagnosticAction),
    "tool_result": ("tool_results", StoredToolResult),
    "evidence": ("evidence", Evidence),
    "memory_relation": ("memory_relations", MemoryRelation),
    "knowledge": ("knowledge", Knowledge),
    "memory_event": ("memory_events", MemoryEvent),
}
