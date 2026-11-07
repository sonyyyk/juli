"""Repository for the Memory Data Model v1: create, read, and change status.

Every write runs in one ``BEGIN IMMEDIATE`` transaction together with its
``memory_events`` row, so a status never changes without a journal entry
(invariant 7). Status changes go through ``transitions`` (allowed moves and
actor rights, invariant 3) and the invariant checks below (1, 2, 4, 5, C11,
C14). Invariant 6 is served by ``list_recommendable_knowledge`` and
``list_knowledge_warnings``.

The repository never calls tools or the LLM; it only records what callers
report. It needs schema version 2 (``ensure_schema()``).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
import sqlite3
from typing import Any

from app.core.database import get_connection
from app.memory.errors import ActorNotAllowed, InvalidTransition, InvariantViolation, NotFound
from app.memory.ids import new_id
from app.memory.models import (
    TABLES,
    DiagnosticAction,
    Evidence,
    Hypothesis,
    Incident,
    IncidentObservation,
    Knowledge,
    MemoryEvent,
    EVIDENTIAL_RELATIONS,
    MemoryRelation,
    StoredToolResult,
    dump_json,
    parse_utc,
    utc_now,
)
from app.memory.transitions import (
    HYPOTHESIS_FINAL,
    INCIDENT_CLOSED,
    check_actor,
    check_create,
    check_transition,
)
from app.tools.registry import get_tool_metadata


MEMORY_SCHEMA_VERSION = 2
# C13: payloads above this size (serialized JSON, UTF-8 bytes) are truncated.
PAYLOAD_LIMIT_BYTES = 64 * 1024
_PREVIEW_MARGIN_BYTES = 256

HYPOTHESIS_ORIGIN_BY_ACTOR = {"llm": "llm", "user": "user", "rule": "memory"}
ACTION_INITIATOR_BY_ACTOR = {"llm": "llm", "user": "user", "rule": "memory_reverification"}


class MemoryRepository:
    """Contextual and long-term memory records in the JULI SQLite database."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection] = get_connection,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._connect = connect
        self._clock = clock

    # --- incidents ----------------------------------------------------------------

    def open_incident(
        self,
        title: str,
        symptom_text: str,
        actor: str,
        host_context: dict[str, Any] | None = None,
        opened_by: str = "user",
        reason: str | None = None,
    ) -> Incident:
        """Open an incident. Only the user opens incidents (D1, C7)."""
        check_create("incident", actor)
        with self._write() as (connection, now):
            incident = Incident(
                id=new_id("incident"),
                title=title,
                symptom_text=symptom_text,
                status="open",
                opened_by=opened_by,
                host_context={} if host_context is None else host_context,
                created_at=now,
                updated_at=now,
            )
            self._insert(connection, incident)
            self._event(connection, "incident", incident.id, "created", actor, now,
                        new=incident.status, reason=reason)
        return incident

    def change_incident_status(
        self,
        incident_id: str,
        new_status: str,
        actor: str,
        reason: str | None = None,
        resolution_summary: str | None = None,
        root_cause_hypothesis_id: str | None = None,
    ) -> Incident:
        with self._write() as (connection, now):
            incident = self._load(connection, "incident", incident_id)
            check_transition("incident", "status", incident.status, new_status, actor)
            changes: dict[str, Any] = {"status": new_status, "updated_at": now}
            event = "status_changed"
            if new_status in INCIDENT_CLOSED:
                changes["closed_at"] = now
                if resolution_summary is not None:
                    changes["resolution_summary"] = resolution_summary
            elif incident.status in INCIDENT_CLOSED:
                # Reopening: the incident is no longer closed and has no accepted root cause.
                event = "reopened"
                changes["closed_at"] = None
                changes["root_cause_hypothesis_id"] = None
            if root_cause_hypothesis_id is not None:
                if new_status != "resolved":
                    raise InvariantViolation("a root cause can only be set when resolving")
                hypothesis = self._load(connection, "hypothesis", root_cause_hypothesis_id)
                if hypothesis.incident_id != incident.id or hypothesis.status != "supported":
                    raise InvariantViolation(
                        "the root cause must be a supported hypothesis of this incident (C14)"
                    )
                changes["root_cause_hypothesis_id"] = hypothesis.id
            updated = self._update(connection, incident, changes)
            self._event(connection, "incident", incident.id, event, actor, now,
                        old=incident.status, new=new_status, reason=reason)
        return updated

    # --- observations -------------------------------------------------------------

    def add_observation(
        self,
        incident_id: str,
        kind: str,
        statement: str,
        observed_at: str,
        freshness: str,
        source_type: str,
        actor: str,
        source_ref: str | None = None,
        resource: str | None = None,
        metric: str | None = None,
        value: float | None = None,
        unit: str | None = None,
    ) -> IncidentObservation:
        check_create("observation", actor)
        with self._write() as (connection, now):
            self._load(connection, "incident", incident_id)
            observation = IncidentObservation(
                id=new_id("observation"),
                incident_id=incident_id,
                kind=kind,
                statement=statement,
                observed_at=observed_at,
                freshness=freshness,
                source_type=source_type,
                source_ref=source_ref,
                resource=resource,
                metric=metric,
                value=value,
                unit=unit,
            )
            if source_type == "snapshot":
                self._require_snapshot(connection, source_ref)
            elif source_type == "tool_result":
                self._load(connection, "tool_result", source_ref)
            self._insert(connection, observation)
            self._event(connection, "observation", observation.id, "created", actor, now)
        return observation

    # --- hypotheses ---------------------------------------------------------------

    def propose_hypothesis(
        self,
        incident_id: str,
        statement: str,
        actor: str,
        origin_knowledge_id: str | None = None,
        reason: str | None = None,
    ) -> Hypothesis:
        """Record a new hypothesis as ``proposed``.

        The origin follows the actor (C7): ``llm`` and ``user`` propose their
        own hypotheses; ``rule`` stands for retrieval and creates
        ``origin = memory`` hypotheses, which need ``origin_knowledge_id``.
        """
        check_create("hypothesis", actor)
        with self._write() as (connection, now):
            self._load(connection, "incident", incident_id)
            if origin_knowledge_id is not None:
                self._load(connection, "knowledge", origin_knowledge_id)
            hypothesis = Hypothesis(
                id=new_id("hypothesis"),
                incident_id=incident_id,
                statement=statement,
                origin=HYPOTHESIS_ORIGIN_BY_ACTOR[actor],
                origin_knowledge_id=origin_knowledge_id,
                status="proposed",
                created_at=now,
                updated_at=now,
            )
            self._insert(connection, hypothesis)
            self._event(connection, "hypothesis", hypothesis.id, "created", actor, now,
                        new="proposed", reason=reason)
        return hypothesis

    def change_hypothesis_status(
        self,
        hypothesis_id: str,
        new_status: str,
        actor: str,
        reason: str | None = None,
    ) -> Hypothesis:
        with self._write() as (connection, now):
            hypothesis = self._load(connection, "hypothesis", hypothesis_id)
            check_transition("hypothesis", "status", hypothesis.status, new_status, actor)
            if new_status == "supported":
                self._check_can_support(connection, hypothesis, actor, reason)
            if hypothesis.status in HYPOTHESIS_FINAL and new_status == "testing":
                self._check_can_retest(connection, hypothesis, actor)
            if hypothesis.status == "supported":
                self._check_not_root_cause(connection, hypothesis)
            updated = self._update(connection, hypothesis, {
                "status": new_status, "status_reason": reason, "updated_at": now,
            })
            self._event(connection, "hypothesis", hypothesis.id, "status_changed", actor, now,
                        old=hypothesis.status, new=new_status, reason=reason)
        return updated

    def _check_can_support(
        self, connection: sqlite3.Connection, hypothesis: Hypothesis, actor: str,
        reason: str | None,
    ) -> None:
        """Invariants 4 and 5 (with C9, C10) for ``-> supported``."""
        links = self._live_links_to(connection, hypothesis.id)
        if any(link.relation == "contradicts" for link in links):
            raise InvariantViolation(
                "the hypothesis has a contradicting relation that is not refuted (invariant 4)"
            )
        tool_evidence = [
            evidence
            for evidence in self._supporting_evidence(connection, hypothesis, links)
            if evidence.source_type == "tool_result"
        ]
        if actor == "user" and not (isinstance(reason, str) and reason.strip()):
            raise InvariantViolation("a user confirmation needs a reason (C9)")
        if hypothesis.origin == "memory":
            if not any(self._is_new_check(connection, hypothesis, ev) for ev in tool_evidence):
                raise InvariantViolation(
                    "a hypothesis from memory needs a new check in this incident: supporting "
                    "evidence from a tool result of this incident's action, obtained after the "
                    "hypothesis was created (invariant 5, C10)"
                )
            return
        if not tool_evidence and actor != "user":
            raise InvariantViolation(
                "a supported hypothesis needs supporting evidence from a tool result or a user "
                "confirmation (invariant 4)"
            )

    def _supporting_evidence(
        self, connection: sqlite3.Connection, hypothesis: Hypothesis,
        links: list[MemoryRelation],
    ) -> list[Evidence]:
        evidence = []
        for link in links:
            if link.relation != "supports" or link.src_type != "evidence":
                continue  # similar_to and other relations never support (invariant 2)
            item = self._load(connection, "evidence", link.src_id)
            if item.incident_id == hypothesis.incident_id:
                evidence.append(item)
        return evidence

    def _is_new_check(
        self, connection: sqlite3.Connection, hypothesis: Hypothesis, evidence: Evidence
    ) -> bool:
        """C10: the tool result came from an action of this incident after the hypothesis."""
        result = self._load(connection, "tool_result", evidence.source_ref)
        action = connection.execute(
            "SELECT 1 FROM diagnostic_actions WHERE tool_result_id = ? AND incident_id = ?",
            (result.id, hypothesis.incident_id),
        ).fetchone()
        return action is not None and _later(result.created_at, hypothesis.created_at)

    def _check_can_retest(
        self, connection: sqlite3.Connection, hypothesis: Hypothesis, actor: str
    ) -> None:
        """C11: back to ``testing`` only by the user or by a rule with new evidence."""
        if actor == "user":
            return
        new_links = [
            link for link in self._live_links_to(connection, hypothesis.id)
            if _later(link.created_at, hypothesis.updated_at)
        ]
        if not new_links:
            raise InvariantViolation(
                "a rule may return a hypothesis to testing only after new evidence "
                "(a supports/contradicts relation created after its last status change, C11)"
            )

    def _check_not_root_cause(self, connection: sqlite3.Connection, hypothesis: Hypothesis) -> None:
        row = connection.execute(
            "SELECT id FROM incidents WHERE root_cause_hypothesis_id = ?", (hypothesis.id,)
        ).fetchone()
        if row is not None:
            raise InvariantViolation(
                f"the hypothesis is the root cause of {row['id']}; reopen the incident first (C14)"
            )

    def _live_links_to(self, connection: sqlite3.Connection, hypothesis_id: str) -> list[MemoryRelation]:
        rows = connection.execute(
            "SELECT * FROM memory_relations WHERE dst_type = 'hypothesis' AND dst_id = ? "
            "AND relation IN ('supports', 'contradicts') AND status != 'refuted'",
            (hypothesis_id,),
        ).fetchall()
        return [MemoryRelation.from_row(row) for row in rows]

    # --- diagnostic actions and tool results --------------------------------------

    def plan_action(
        self,
        incident_id: str,
        kind: str,
        tool_name: str,
        actor: str,
        parameters: dict[str, Any] | None = None,
        hypothesis_id: str | None = None,
        reason: str | None = None,
    ) -> DiagnosticAction:
        """Plan a check. ``tool_name`` must be in the Tool Registry; ``read_check``
        needs a read tool and ``action`` a non-read tool (none exist in v1)."""
        check_create("diagnostic_action", actor)
        metadata = get_tool_metadata(tool_name) if isinstance(tool_name, str) else None
        if metadata is None:
            raise ValueError(f"tool {tool_name!r} is not in the Tool Registry")
        if (kind == "read_check") != (metadata.get("operation") == "read"):
            raise ValueError(f"tool {tool_name!r} does not match action kind {kind!r}")
        with self._write() as (connection, now):
            self._load(connection, "incident", incident_id)
            if hypothesis_id is not None:
                hypothesis = self._load(connection, "hypothesis", hypothesis_id)
                if hypothesis.incident_id != incident_id:
                    raise InvariantViolation("the hypothesis belongs to another incident")
            action = DiagnosticAction(
                id=new_id("diagnostic_action"),
                incident_id=incident_id,
                hypothesis_id=hypothesis_id,
                kind=kind,
                tool_name=tool_name,
                parameters={} if parameters is None else parameters,
                initiator=ACTION_INITIATOR_BY_ACTOR[actor],
                approval="not_required" if kind == "read_check" else "pending",
                status="planned",
            )
            self._insert(connection, action)
            self._event(connection, "diagnostic_action", action.id, "created", actor, now,
                        new="planned", reason=reason)
        return action

    def change_action_status(
        self,
        action_id: str,
        new_status: str,
        actor: str,
        reason: str | None = None,
        tool_result_id: str | None = None,
    ) -> DiagnosticAction:
        """Move an action through ``planned -> running -> succeeded | failed``.

        ``succeeded`` means the tool ran, not that the problem is solved; it
        needs the stored tool result of this run.
        """
        with self._write() as (connection, now):
            action = self._load(connection, "diagnostic_action", action_id)
            check_transition("diagnostic_action", "status", action.status, new_status, actor)
            changes: dict[str, Any] = {"status": new_status}
            if new_status == "running":
                if action.approval not in ("not_required", "approved"):
                    raise InvariantViolation(
                        f"the action cannot run with approval {action.approval!r}"
                    )
                changes["started_at"] = now
            else:
                changes["finished_at"] = now
            if new_status == "succeeded" and tool_result_id is None:
                raise InvariantViolation("a succeeded action needs its tool result")
            if tool_result_id is not None:
                if new_status not in ("succeeded", "failed"):
                    raise InvariantViolation("a tool result is attached when the run finishes")
                result = self._load(connection, "tool_result", tool_result_id)
                if result.tool_name != action.tool_name:
                    raise InvariantViolation("the tool result comes from another tool")
                used = connection.execute(
                    "SELECT id FROM diagnostic_actions WHERE tool_result_id = ?", (result.id,)
                ).fetchone()
                if used is not None:
                    raise InvariantViolation(f"the tool result already belongs to {used['id']}")
                changes["tool_result_id"] = result.id
            updated = self._update(connection, action, changes)
            self._event(connection, "diagnostic_action", action.id, "status_changed", actor, now,
                        old=action.status, new=new_status, reason=reason)
        return updated

    def change_action_approval(
        self, action_id: str, new_approval: str, actor: str, reason: str | None = None
    ) -> DiagnosticAction:
        """Approve or reject a pending action; only the user may do this (C8)."""
        with self._write() as (connection, now):
            action = self._load(connection, "diagnostic_action", action_id)
            check_transition("diagnostic_action", "approval", action.approval, new_approval, actor)
            updated = self._update(connection, action, {"approval": new_approval})
            self._event(connection, "diagnostic_action", action.id, "status_changed", actor, now,
                        old=f"approval:{action.approval}", new=f"approval:{new_approval}",
                        reason=reason)
        return updated

    def record_tool_result(
        self,
        tool_result: dict[str, Any],
        actor: str,
        parameters: dict[str, Any] | None = None,
    ) -> StoredToolResult:
        """Store a ToolResult (D4). Only the deterministic Tool Layer (``rule``) records
        tool output: neither the LLM nor the user may supply it."""
        check_create("tool_result", actor)
        if not isinstance(tool_result, dict):
            raise ValueError("tool_result must be a ToolResult mapping")
        missing = {"tool", "status", "data", "error"} - set(tool_result)
        if missing:
            raise ValueError(f"tool_result is missing keys: {', '.join(sorted(missing))}")
        with self._write() as (connection, now):
            stored = StoredToolResult(
                id=new_id("tool_result"),
                tool_name=tool_result["tool"],
                status=tool_result["status"],
                parameters={} if parameters is None else parameters,
                payload=_limit_payload(tool_result["data"]),
                error=tool_result["error"],
                created_at=now,
            )
            self._insert(connection, stored)
            self._event(connection, "tool_result", stored.id, "created", actor, now,
                        new=stored.status)
        return stored

    # --- evidence and relations ---------------------------------------------------

    def add_evidence(
        self,
        incident_id: str,
        summary: str,
        source_type: str,
        quality: str,
        actor: str,
        source_ref: str | None = None,
    ) -> Evidence:
        """Record evidence; ``assessed_by`` is the actor that assessed it."""
        check_create("evidence", actor)
        with self._write() as (connection, now):
            self._load(connection, "incident", incident_id)
            evidence = Evidence(
                id=new_id("evidence"),
                incident_id=incident_id,
                summary=summary,
                source_type=source_type,
                source_ref=source_ref,
                quality=quality,
                assessed_by=actor,
                created_at=now,
            )
            if source_type == "tool_result":
                self._load(connection, "tool_result", source_ref)
            elif source_type == "observation":
                observation = self._load(connection, "observation", source_ref)
                if observation.incident_id != incident_id:
                    raise InvariantViolation("the observation belongs to another incident")
            self._insert(connection, evidence)
            self._event(connection, "evidence", evidence.id, "created", actor, now)
        return evidence

    def link(
        self,
        src_type: str,
        src_id: str,
        relation: str,
        relation_class: str,
        dst_type: str,
        dst_id: str,
        actor: str,
        status: str = "asserted",
        basis_evidence_id: str | None = None,
        reason: str | None = None,
    ) -> MemoryRelation:
        """Create a relation. The LLM may only assert; ``verified`` at creation needs a
        rule or the user and, for causal relations, a basis evidence (invariant 1)."""
        check_create("memory_relation", actor)
        if status not in ("asserted", "verified"):
            raise InvalidTransition("a new relation is asserted or verified")
        if status != "asserted" and actor == "llm":
            raise ActorNotAllowed("the LLM may only assert relations (invariant 3)")
        if relation == "similar_to" and relation_class != "associative":
            raise InvariantViolation("similar_to is always associative (invariant 2)")
        if relation in EVIDENTIAL_RELATIONS and relation_class != "evidential":
            raise InvariantViolation(f"{relation} is always an evidential relation (C15)")
        with self._write() as (connection, now):
            relation_record = MemoryRelation(
                id=new_id("memory_relation"),
                src_type=src_type,
                src_id=src_id,
                relation=relation,
                relation_class=relation_class,
                dst_type=dst_type,
                dst_id=dst_id,
                status=status,
                basis_evidence_id=basis_evidence_id,
                created_by=actor,
                created_at=now,
            )
            if (src_type, src_id) == (dst_type, dst_id):
                raise InvariantViolation("a relation needs two different records")
            self._load(connection, src_type, src_id)
            self._load(connection, dst_type, dst_id)
            if basis_evidence_id is not None:
                self._load(connection, "evidence", basis_evidence_id)
            _check_causal_basis(relation_record)
            self._insert(connection, relation_record)
            self._event(connection, "memory_relation", relation_record.id, "linked", actor, now,
                        new=status, reason=reason)
        return relation_record

    def change_relation_status(
        self,
        relation_id: str,
        new_status: str,
        actor: str,
        reason: str | None = None,
        basis_evidence_id: str | None = None,
    ) -> MemoryRelation:
        with self._write() as (connection, now):
            relation = self._load(connection, "memory_relation", relation_id)
            check_transition("memory_relation", "status", relation.status, new_status, actor)
            changes: dict[str, Any] = {"status": new_status}
            if basis_evidence_id is not None:
                self._load(connection, "evidence", basis_evidence_id)
                changes["basis_evidence_id"] = basis_evidence_id
            candidate = MemoryRelation(**{**relation.__dict__, **changes})
            _check_causal_basis(candidate)
            updated = self._update(connection, relation, changes)
            self._event(connection, "memory_relation", relation.id, "status_changed", actor, now,
                        old=relation.status, new=new_status, reason=reason)
        return updated

    # --- knowledge (long-term memory) ---------------------------------------------

    def create_knowledge(
        self,
        kind: str,
        statement: str,
        actor: str,
        status: str = "unverified",
        symptom_signature: dict[str, Any] | None = None,
        verification_procedure: list[Any] | None = None,
        scope: dict[str, Any] | None = None,
        source_incident_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> Knowledge:
        check_create("knowledge", actor)
        if status == "deprecated":
            raise InvalidTransition("new knowledge cannot be deprecated")
        with self._write() as (connection, now):
            knowledge = Knowledge(
                id=new_id("knowledge"),
                kind=kind,
                statement=statement,
                status=status,
                symptom_signature={} if symptom_signature is None else symptom_signature,
                verification_procedure=[] if verification_procedure is None else verification_procedure,
                scope={} if scope is None else scope,
                source_incident_ids=[] if source_incident_ids is None else source_incident_ids,
                created_at=now,
                updated_at=now,
            )
            for incident_id in knowledge.source_incident_ids:
                self._load(connection, "incident", incident_id)
            self._insert(connection, knowledge)
            self._event(connection, "knowledge", knowledge.id, "created", actor, now,
                        new=status, reason=reason)
        return knowledge

    def change_knowledge_status(
        self,
        knowledge_id: str,
        new_status: str,
        actor: str,
        reason: str | None = None,
        superseded_by: str | None = None,
    ) -> Knowledge:
        with self._write() as (connection, now):
            knowledge = self._load(connection, "knowledge", knowledge_id)
            check_transition("knowledge", "status", knowledge.status, new_status, actor)
            changes: dict[str, Any] = {"status": new_status, "updated_at": now}
            if superseded_by is not None:
                if new_status != "deprecated":
                    raise InvariantViolation("superseded_by is set only when deprecating")
                if superseded_by == knowledge.id:
                    raise InvariantViolation("knowledge cannot supersede itself")
                self._load(connection, "knowledge", superseded_by)
                changes["superseded_by"] = superseded_by
            updated = self._update(connection, knowledge, changes)
            self._event(connection, "knowledge", knowledge.id, "status_changed", actor, now,
                        old=knowledge.status, new=new_status, reason=reason)
        return updated

    def list_recommendable_knowledge(self) -> list[Knowledge]:
        """Knowledge that retrieval may offer as a candidate: ``verified`` and
        ``unverified`` (invariant 6, C4). ``failed`` is only a warning and
        ``deprecated`` is skipped entirely. Candidates still become hypotheses
        that must be re-verified."""
        return self._query(
            Knowledge, "SELECT * FROM knowledge WHERE status IN ('verified', 'unverified')"
        )

    def list_knowledge_warnings(self) -> list[Knowledge]:
        """``failed`` knowledge, offered only as a warning that it did not work
        (invariant 6). ``deprecated`` knowledge is not returned here either (C4)."""
        return self._query(Knowledge, "SELECT * FROM knowledge WHERE status = 'failed'")

    # --- reading ------------------------------------------------------------------

    def get(self, entity_type: str, record_id: str):
        """Return one record by type and id, or raise ``NotFound``."""
        with self._read() as connection:
            return self._load(connection, entity_type, record_id)

    def list_incidents(self, status: str | None = None) -> list[Incident]:
        if status is None:
            return self._query(Incident, "SELECT * FROM incidents")
        return self._query(Incident, "SELECT * FROM incidents WHERE status = ?", (status,))

    def list_for_incident(self, entity_type: str, incident_id: str) -> list:
        if entity_type not in ("observation", "hypothesis", "diagnostic_action", "evidence"):
            raise ValueError(f"{entity_type!r} records are not stored per incident")
        table, model = TABLES[entity_type]
        return self._query(model, f"SELECT * FROM {table} WHERE incident_id = ?", (incident_id,))

    def list_relations(self, entity_type: str, entity_id: str) -> list[MemoryRelation]:
        """Relations in which the record is the source or the destination."""
        return self._query(
            MemoryRelation,
            "SELECT * FROM memory_relations WHERE (src_type = ? AND src_id = ?) "
            "OR (dst_type = ? AND dst_id = ?)",
            (entity_type, entity_id, entity_type, entity_id),
        )

    def list_events(
        self, entity_type: str | None = None, entity_id: str | None = None
    ) -> list[MemoryEvent]:
        """Journal entries in the order they were written."""
        sql, params = "SELECT * FROM memory_events", []
        conditions = []
        if entity_type is not None:
            conditions.append("entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            conditions.append("entity_id = ?")
            params.append(entity_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        return self._query(MemoryEvent, sql, tuple(params))

    def list_incident_events(self, incident_id: str) -> list[MemoryEvent]:
        """The incident's journal together with the events of every record linked to it.

        Linked records: its observations, hypotheses, diagnostic actions,
        evidence, the tool results of its actions, and the relations touching
        any of these. Events come in chronological order (time, then write order).
        """
        with self._read() as connection:
            incident = self._load(connection, "incident", incident_id)
            ids = {incident.id}
            for table in ("observations", "hypotheses", "diagnostic_actions", "evidence"):
                ids.update(row[0] for row in connection.execute(
                    f"SELECT id FROM {table} WHERE incident_id = ?", (incident.id,)))
            ids.update(row[0] for row in connection.execute(
                "SELECT tool_result_id FROM diagnostic_actions "
                "WHERE incident_id = ? AND tool_result_id IS NOT NULL", (incident.id,)))
            relations = connection.execute(
                "SELECT id, src_id, dst_id FROM memory_relations").fetchall()
            ids.update(row["id"] for row in relations
                       if row["src_id"] in ids or row["dst_id"] in ids)
            rows = connection.execute(
                "SELECT rowid AS position, * FROM memory_events ORDER BY rowid").fetchall()
        events = [
            (parse_utc(row["at"]), row["position"], MemoryEvent.from_row(row))
            for row in rows if row["entity_id"] in ids
        ]
        events.sort(key=lambda item: (item[0], item[1]))
        return [event for _, _, event in events]

    def resolve_incident_id(self, reference: str, min_prefix: int = 8) -> str:
        """Return the full incident id for a full id or a unique prefix.

        The prefix is the hex part after ``inc_`` (``inc_`` itself is optional)
        and needs at least ``min_prefix`` characters. An ambiguous prefix
        raises ``ValueError`` listing the matches.
        """
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("an incident id or prefix is required")
        reference = reference.strip()
        prefix = reference if reference.startswith("inc_") else "inc_" + reference
        if len(prefix) - len("inc_") < min_prefix:
            raise ValueError(
                f"incident id prefix {reference!r} is too short; use at least {min_prefix} "
                "characters after 'inc_'"
            )
        with self._read() as connection:
            matches = [row[0] for row in connection.execute(
                "SELECT id FROM incidents WHERE substr(id, 1, ?) = ? ORDER BY rowid",
                (len(prefix), prefix),
            )]
        if not matches:
            raise NotFound(f"no incident matches {reference!r}")
        if len(matches) > 1:
            raise ValueError(
                f"incident id prefix {reference!r} is ambiguous; it matches: " + ", ".join(matches)
            )
        return matches[0]

    # --- internals ----------------------------------------------------------------

    @contextmanager
    def _write(self) -> Iterator[tuple[sqlite3.Connection, str]]:
        connection = self._open()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection, self._clock()
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = self._connect()
        try:
            connection.row_factory = sqlite3.Row
            connection.isolation_level = None
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise RuntimeError("memory connections must enforce foreign keys")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < MEMORY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"memory tables need schema version {MEMORY_SCHEMA_VERSION}, found {version}; "
                    "run ensure_schema() first"
                )
        except BaseException:
            connection.close()
            raise
        return connection

    def _query(self, model, sql: str, params: tuple = ()) -> list:
        with self._read() as connection:
            rows = connection.execute(sql + " ORDER BY rowid", params).fetchall()
        return [model.from_row(row) for row in rows]

    def _load(self, connection: sqlite3.Connection, entity_type: str, record_id: object):
        if entity_type not in TABLES:
            raise ValueError(f"unknown entity type: {entity_type!r}")
        table, model = TABLES[entity_type]
        row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"{entity_type} {record_id!r} does not exist")
        return model.from_row(row)

    def _require_snapshot(self, connection: sqlite3.Connection, source_ref: str | None) -> None:
        try:
            snapshot_id = int(source_ref)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"snapshot source_ref must be a snapshot id; got {source_ref!r}") from None
        row = connection.execute(
            "SELECT 1 FROM system_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"snapshot {source_ref!r} does not exist")

    def _insert(self, connection: sqlite3.Connection, record) -> None:
        table = _table_of(record)
        values = record.to_row()
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))

    def _update(self, connection: sqlite3.Connection, record, changes: dict[str, Any]):
        updated = type(record)(**{**record.__dict__, **changes})  # re-validates every field
        values = {name: updated.to_row()[name] for name in changes}
        assignments = ", ".join(f"{name} = ?" for name in values)
        connection.execute(
            f"UPDATE {_table_of(record)} SET {assignments} WHERE id = ?",
            (*values.values(), record.id),
        )
        return updated

    def _event(
        self,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        event: str,
        actor: str,
        at: str,
        old: str | None = None,
        new: str | None = None,
        reason: str | None = None,
    ) -> None:
        check_actor(actor)
        self._insert(connection, MemoryEvent(
            id=new_id("memory_event"),
            entity_type=entity_type,
            entity_id=entity_id,
            event=event,
            actor=actor,
            at=at,
            old_value=old,
            new_value=new,
            reason=reason,
        ))


def _table_of(record) -> str:
    for table, model in TABLES.values():
        if type(record) is model:
            return table
    raise TypeError(f"not a memory record: {type(record).__name__}")


def _check_causal_basis(relation: MemoryRelation) -> None:
    if (
        relation.relation_class == "causal"
        and relation.status == "verified"
        and relation.basis_evidence_id is None
    ):
        raise InvariantViolation("a verified causal relation needs basis_evidence_id (invariant 1)")


def _later(value: str, reference: str) -> bool:
    moment, base = parse_utc(value), parse_utc(reference)
    return moment is not None and base is not None and moment > base


def _limit_payload(data: Any) -> dict[str, Any] | None:
    """C13: keep payloads up to the limit; replace larger ones with a marked preview."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("ToolResult data must be a mapping or None")
    text = dump_json(data)
    size = len(text.encode("utf-8"))
    if size <= PAYLOAD_LIMIT_BYTES:
        return json.loads(text)
    budget = PAYLOAD_LIMIT_BYTES - _PREVIEW_MARGIN_BYTES
    preview = text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return {"truncated": True, "original_size_bytes": size, "preview": preview}
