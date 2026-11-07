"""Repository behaviour: create, read, transitions, references, tool results (M1)."""

import sqlite3

import pytest

from conftest import SUCCESS, disk_check, hypothesis_in_testing, investigating_incident

from app.core import database
from app.memory import (
    ActorNotAllowed,
    InvalidTransition,
    InvariantViolation,
    MemoryRepository,
    NotFound,
)
from app.memory import repository as repository_module


def add_snapshot(path):
    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "INSERT INTO system_snapshots (timestamp, cpu_usage, memory_usage, disk_usage) "
            "VALUES ('2026-11-07T09:59:00+00:00', 10.0, 37.0, 89.3)"
        )
        connection.commit()
        return str(cursor.lastrowid)
    finally:
        connection.close()


def test_repository_refuses_a_database_without_the_memory_schema(tmp_path, monkeypatch):
    path = tmp_path / "juli.db"
    monkeypatch.setenv("JULI_DB_PATH", str(path))
    database.MIGRATIONS[0][1](sqlite3.connect(path))
    sqlite3.connect(path).execute("PRAGMA user_version = 1")

    with pytest.raises(RuntimeError, match="schema version 2"):
        MemoryRepository().list_incidents()


def test_incident_lifecycle_records_and_reads_back(repo):
    incident = repo.open_incident("Disk almost full", "Диск майже повний", actor="user",
                                  host_context={"hostname": "box"}, opened_by="juli_suggested")
    assert (incident.status, incident.opened_by, incident.closed_at) == ("open", "juli_suggested", None)
    assert repo.get("incident", incident.id) == incident

    with pytest.raises(InvalidTransition):
        repo.change_incident_status(incident.id, "resolved", actor="user")
    repo.change_incident_status(incident.id, "investigating", actor="rule")
    closed = repo.change_incident_status(incident.id, "unresolved", actor="user",
                                         resolution_summary="Cause not found")
    assert closed.closed_at is not None and closed.resolution_summary == "Cause not found"
    assert [i.id for i in repo.list_incidents("unresolved")] == [incident.id]
    assert repo.list_incidents("open") == []


def test_get_raises_not_found_and_rejects_unknown_types(repo):
    with pytest.raises(NotFound):
        repo.get("incident", "inc_missing")
    with pytest.raises(ValueError):
        repo.get("snapshot", "1")


def test_observation_references_are_checked(repo, memory_db):
    incident = investigating_incident(repo)
    snapshot_id = add_snapshot(memory_db)
    observation = repo.add_observation(
        incident.id, "metric", "disk usage 89.3%", "2026-11-07T09:59:00+00:00", "fresh",
        "snapshot", actor="rule", source_ref=snapshot_id, resource="disk",
        metric="usage_percent", value=89.3, unit="%",
    )
    assert repo.list_for_incident("observation", incident.id) == [observation]

    with pytest.raises(NotFound):
        repo.add_observation(incident.id, "metric", "disk usage 50%", "2026-11-07T09:59:00+00:00",
                             "fresh", "snapshot", actor="rule", source_ref="999999")
    with pytest.raises(ValueError, match="snapshot id"):
        repo.add_observation(incident.id, "metric", "disk usage 50%", "2026-11-07T09:59:00+00:00",
                             "fresh", "snapshot", actor="rule", source_ref="obs_1")
    with pytest.raises(NotFound):
        repo.add_observation(incident.id, "state", "disk usage 50%", "2026-11-07T09:59:00+00:00",
                             "fresh", "tool_result", actor="rule", source_ref="tr_missing")
    with pytest.raises(NotFound):
        repo.add_observation("inc_missing", "symptom", "disk is full", "2026-11-07T09:59:00+00:00",
                             "unknown", "user", actor="user")


def test_evidence_from_an_observation_must_belong_to_the_same_incident(repo):
    incident = investigating_incident(repo)
    other = investigating_incident(repo)
    observation = repo.add_observation(other.id, "symptom", "disk is full",
                                       "2026-11-07T09:59:00+00:00", "unknown", "user", actor="user")
    with pytest.raises(InvariantViolation, match="another incident"):
        repo.add_evidence(incident.id, "User report", "observation", "stale", actor="user",
                          source_ref=observation.id)


def test_hypothesis_origin_follows_the_actor(repo):
    incident = investigating_incident(repo)
    assert repo.propose_hypothesis(incident.id, "Logs fill the disk", actor="user").origin == "user"
    assert repo.propose_hypothesis(incident.id, "Logs fill the disk", actor="llm").origin == "llm"
    with pytest.raises(ValueError, match="origin_knowledge_id"):
        repo.propose_hypothesis(incident.id, "Logs fill the disk", actor="rule")
    with pytest.raises(NotFound):
        repo.propose_hypothesis(incident.id, "Logs fill the disk", actor="rule",
                                origin_knowledge_id="kn_missing")
    with pytest.raises(ValueError, match="English"):
        repo.propose_hypothesis(incident.id, "Логи заповнюють диск", actor="llm")
    assert len(repo.list_for_incident("hypothesis", incident.id)) == 2


def test_hypothesis_transitions_follow_the_specification(repo):
    incident = investigating_incident(repo)
    hypothesis = repo.propose_hypothesis(incident.id, "Logs fill the disk", actor="llm")
    with pytest.raises(InvalidTransition):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="user", reason="r")
    repo.change_hypothesis_status(hypothesis.id, "testing", actor="llm")
    with pytest.raises(InvalidTransition):
        repo.change_hypothesis_status(hypothesis.id, "proposed", actor="user")
    refuted = repo.change_hypothesis_status(hypothesis.id, "refuted", actor="rule",
                                            reason="journal is small")
    assert (refuted.status, refuted.status_reason) == ("refuted", "journal is small")


def test_actions_need_registered_tools_of_the_right_kind(repo):
    incident = investigating_incident(repo)
    with pytest.raises(ValueError, match="Tool Registry"):
        repo.plan_action(incident.id, "read_check", "rm_rf", actor="llm")
    with pytest.raises(ValueError, match="does not match"):
        repo.plan_action(incident.id, "action", "get_disk_usage", actor="user")
    other = investigating_incident(repo)
    foreign = repo.propose_hypothesis(other.id, "Logs fill the disk", actor="llm")
    with pytest.raises(InvariantViolation, match="another incident"):
        repo.plan_action(incident.id, "read_check", "get_disk_usage", actor="llm",
                         hypothesis_id=foreign.id)


def test_action_run_needs_approval_and_a_matching_tool_result(repo, monkeypatch):
    from app.tools import registry

    monkeypatch.setitem(registry.TOOL_REGISTRY, "restart_service", lambda: None)
    monkeypatch.setitem(registry.TOOL_METADATA, "restart_service", {"operation": "action"})
    incident = investigating_incident(repo)
    risky = repo.plan_action(incident.id, "action", "restart_service", actor="user")
    with pytest.raises(InvariantViolation, match="approval 'pending'"):
        repo.change_action_status(risky.id, "running", actor="rule")
    repo.change_action_approval(risky.id, "rejected", actor="user")
    with pytest.raises(InvariantViolation, match="approval 'rejected'"):
        repo.change_action_status(risky.id, "running", actor="rule")
    with pytest.raises(InvalidTransition):
        repo.change_action_approval(risky.id, "approved", actor="user")

    check = repo.plan_action(incident.id, "read_check", "get_disk_usage", actor="llm")
    running = repo.change_action_status(check.id, "running", actor="rule")
    assert running.started_at is not None
    with pytest.raises(InvariantViolation, match="needs its tool result"):
        repo.change_action_status(check.id, "succeeded", actor="rule")
    wrong = repo.record_tool_result({**SUCCESS, "tool": "get_cpu_usage"}, actor="rule")
    with pytest.raises(InvariantViolation, match="another tool"):
        repo.change_action_status(check.id, "succeeded", actor="rule", tool_result_id=wrong.id)
    result = repo.record_tool_result(SUCCESS, actor="rule")
    done = repo.change_action_status(check.id, "succeeded", actor="rule", tool_result_id=result.id)
    assert (done.tool_result_id, done.finished_at is not None) == (result.id, True)

    second = repo.plan_action(incident.id, "read_check", "get_disk_usage", actor="llm")
    repo.change_action_status(second.id, "running", actor="rule")
    with pytest.raises(InvariantViolation, match="already belongs"):
        repo.change_action_status(second.id, "failed", actor="rule", tool_result_id=result.id)


def test_not_required_approval_cannot_change(repo):
    incident = investigating_incident(repo)
    check = repo.plan_action(incident.id, "read_check", "get_disk_usage", actor="llm")
    with pytest.raises(InvalidTransition):
        repo.change_action_approval(check.id, "approved", actor="user")


def test_tool_results_keep_data_and_error(repo):
    partial = repo.record_tool_result({
        "tool": "get_system_state", "status": "partial",
        "data": {"cpu": {"percent": 10.0}, "memory": None},
        "error": {"code": "SYSTEM_STATE_PARTIAL", "message": "memory failed"},
    }, actor="rule", parameters={})
    stored = repo.get("tool_result", partial.id)
    assert stored.payload == {"cpu": {"percent": 10.0}, "memory": None}
    assert stored.error["code"] == "SYSTEM_STATE_PARTIAL"
    failed = repo.record_tool_result({"tool": "get_disk_usage", "status": "error", "data": None,
                                      "error": {"code": "X", "message": "m"}}, actor="rule")
    assert repo.get("tool_result", failed.id).payload is None
    with pytest.raises(ValueError, match="missing keys"):
        repo.record_tool_result({"tool": "get_disk_usage", "status": "success"}, actor="rule")


def test_large_payload_is_truncated_with_a_marker(repo, monkeypatch):
    monkeypatch.setattr(repository_module, "PAYLOAD_LIMIT_BYTES", 1000)
    data = {"processes": [{"name": f"proc-{i}", "comment": "ї" * 20} for i in range(100)]}
    stored = repo.record_tool_result({**SUCCESS, "tool": "list_processes", "data": data},
                                     actor="rule")
    payload = repo.get("tool_result", stored.id).payload
    assert payload["truncated"] is True
    assert payload["original_size_bytes"] > 1000
    assert len(payload["preview"].encode("utf-8")) <= 1000
    assert payload["preview"].startswith('{"processes": [{"comment": "ї')

    small = repo.record_tool_result(SUCCESS, actor="rule")
    assert repo.get("tool_result", small.id).payload == {"percent": 89.3}


def test_relations_check_their_endpoints(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    with pytest.raises(NotFound):
        repo.link("evidence", "ev_missing", "supports", "evidential", "hypothesis",
                  hypothesis.id, actor="llm")
    with pytest.raises(InvariantViolation, match="two different"):
        repo.link("hypothesis", hypothesis.id, "depends_on", "semantic", "hypothesis",
                  hypothesis.id, actor="llm")
    with pytest.raises(InvalidTransition):
        repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                  hypothesis.id, actor="rule", status="refuted")
    relation = repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                         hypothesis.id, actor="llm")
    assert repo.list_relations("hypothesis", hypothesis.id) == [relation]
    assert repo.list_relations("evidence", evidence.id) == [relation]
    repo.change_relation_status(relation.id, "refuted", actor="user")
    with pytest.raises(InvalidTransition):
        repo.change_relation_status(relation.id, "verified", actor="user")


def test_knowledge_lifecycle(repo):
    incident = investigating_incident(repo)
    with pytest.raises(InvalidTransition):
        repo.create_knowledge("procedure", "Check the journal", actor="rule", status="deprecated")
    with pytest.raises(NotFound):
        repo.create_knowledge("procedure", "Check the journal", actor="rule",
                              source_incident_ids=["inc_missing"])
    old = repo.create_knowledge(
        "root_cause_pattern", "Journal logs fill the disk", actor="rule",
        symptom_signature={"resource": "disk", "metric": "usage_percent", "condition": "> 85"},
        verification_procedure=[{"tool": "get_disk_usage"}], scope={"os": "Ubuntu"},
        source_incident_ids=[incident.id],
    )
    stored = repo.get("knowledge", old.id)
    assert stored.symptom_signature["condition"] == "> 85"
    assert stored.source_incident_ids == [incident.id]
    new = repo.create_knowledge("root_cause_pattern", "Journal logs fill the disk", actor="rule")
    with pytest.raises(InvariantViolation, match="only when deprecating"):
        repo.change_knowledge_status(old.id, "verified", actor="rule", superseded_by=new.id)
    deprecated = repo.change_knowledge_status(old.id, "deprecated", actor="rule",
                                              superseded_by=new.id)
    assert deprecated.superseded_by == new.id
    with pytest.raises(InvalidTransition):
        repo.change_knowledge_status(old.id, "verified", actor="rule")


def test_reads_return_records_in_insertion_order(repo):
    incident = investigating_incident(repo)
    ids = [repo.propose_hypothesis(incident.id, f"Hypothesis {n}", actor="llm").id for n in range(5)]
    assert [h.id for h in repo.list_for_incident("hypothesis", incident.id)] == ids
    with pytest.raises(ValueError):
        repo.list_for_incident("knowledge", incident.id)


def test_invalid_actor_is_rejected(repo):
    with pytest.raises(ValueError, match="actor"):
        repo.open_incident("Disk", "disk", actor="system")
    with pytest.raises(ActorNotAllowed):
        repo.open_incident("Disk", "disk", actor="rule")
