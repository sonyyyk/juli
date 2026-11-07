"""Invariants 1-7 (specification section 3) with the clarifications C9-C11, C14 (section 9)."""

import pytest

from conftest import SUCCESS, disk_check, investigating_incident, hypothesis_in_testing

from app.memory import ActorNotAllowed, InvalidTransition, InvariantViolation, MemoryRepository


# --- invariant 1: a verified causal relation needs basis evidence -------------------------


def test_1_verified_causal_relation_without_basis_is_rejected_at_creation(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)

    with pytest.raises(InvariantViolation, match="invariant 1"):
        repo.link("evidence", evidence.id, "causes", "causal", "hypothesis", hypothesis.id,
                  actor="rule", status="verified")

    relation = repo.link("evidence", evidence.id, "causes", "causal", "hypothesis",
                         hypothesis.id, actor="rule", status="verified",
                         basis_evidence_id=evidence.id)
    assert relation.status == "verified"


def test_1_causal_relation_becomes_verified_only_with_basis(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    relation = repo.link("evidence", evidence.id, "causes", "causal", "hypothesis",
                         hypothesis.id, actor="llm")

    with pytest.raises(InvariantViolation, match="invariant 1"):
        repo.change_relation_status(relation.id, "verified", actor="rule")
    assert repo.get("memory_relation", relation.id).status == "asserted"

    verified = repo.change_relation_status(relation.id, "verified", actor="rule",
                                           basis_evidence_id=evidence.id)
    assert verified.basis_evidence_id == evidence.id


def test_1_non_causal_relation_may_be_verified_without_basis(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    relation = repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                         hypothesis.id, actor="llm")

    assert repo.change_relation_status(relation.id, "verified", actor="user").status == "verified"


# --- invariant 2: similar_to is associative and never supports -----------------------------


@pytest.mark.parametrize("relation_class", ["semantic", "evidential", "causal"])
def test_2_similar_to_must_be_associative(repo, relation_class):
    incident = investigating_incident(repo)
    first = repo.propose_hypothesis(incident.id, "Journal logs fill the disk", actor="llm")
    second = repo.propose_hypothesis(incident.id, "Docker images fill the disk", actor="llm")

    with pytest.raises(InvariantViolation, match="invariant 2"):
        repo.link("hypothesis", first.id, "similar_to", relation_class, "hypothesis",
                  second.id, actor="rule")
    assert repo.link("hypothesis", first.id, "similar_to", "associative", "hypothesis",
                     second.id, actor="rule").relation_class == "associative"


def test_2_similar_to_from_tool_evidence_does_not_make_a_hypothesis_supported(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    repo.link("evidence", evidence.id, "similar_to", "associative", "hypothesis",
              hypothesis.id, actor="rule", status="verified")

    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


# --- invariant 3: the LLM only proposes ------------------------------------------------------


@pytest.mark.parametrize("new_status", ["supported", "refuted", "inconclusive"])
def test_3_llm_cannot_conclude_a_hypothesis(repo, new_status):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="llm")

    with pytest.raises(ActorNotAllowed):
        repo.change_hypothesis_status(hypothesis.id, new_status, actor="llm", reason="sure")
    assert repo.get("hypothesis", hypothesis.id).status == "testing"


def test_3_llm_cannot_verify_or_refute_relations(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)

    with pytest.raises(ActorNotAllowed):
        repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                  hypothesis.id, actor="llm", status="verified")
    relation = repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                         hypothesis.id, actor="llm")
    for new_status in ("verified", "refuted"):
        with pytest.raises(ActorNotAllowed):
            repo.change_relation_status(relation.id, new_status, actor="llm")


def test_3_llm_cannot_open_or_change_incidents(repo):
    with pytest.raises(ActorNotAllowed):
        repo.open_incident("Disk almost full", "disk", actor="llm")
    incident = repo.open_incident("Disk almost full", "disk", actor="user")
    with pytest.raises(ActorNotAllowed):
        repo.change_incident_status(incident.id, "investigating", actor="llm")


def test_3_llm_cannot_write_knowledge_or_tool_output(repo):
    with pytest.raises(ActorNotAllowed):
        repo.create_knowledge("procedure", "Check the journal size", actor="llm")
    with pytest.raises(ActorNotAllowed):
        repo.record_tool_result(SUCCESS, actor="llm")
    knowledge = repo.create_knowledge("procedure", "Check the journal size", actor="rule")
    with pytest.raises(ActorNotAllowed):
        repo.change_knowledge_status(knowledge.id, "verified", actor="llm")


def test_3_user_cannot_record_tool_output_either(repo):
    with pytest.raises(ActorNotAllowed):
        repo.record_tool_result(SUCCESS, actor="user")


def test_3_llm_cannot_run_actions_and_only_the_user_approves(repo, monkeypatch):
    from app.tools import registry

    monkeypatch.setitem(registry.TOOL_REGISTRY, "restart_service", lambda: None)
    monkeypatch.setitem(registry.TOOL_METADATA, "restart_service", {"operation": "action"})
    incident = investigating_incident(repo)
    action = repo.plan_action(incident.id, "action", "restart_service", actor="llm")
    assert action.approval == "pending"

    for actor in ("llm", "rule"):
        with pytest.raises(ActorNotAllowed):
            repo.change_action_approval(action.id, "approved", actor=actor)
    with pytest.raises(ActorNotAllowed):
        repo.change_action_status(action.id, "skipped", actor="llm")
    assert repo.change_action_approval(action.id, "approved", actor="user").approval == "approved"


def test_3_llm_may_create_records_and_start_testing(repo):
    incident = investigating_incident(repo)
    hypothesis = repo.propose_hypothesis(incident.id, "Journal logs fill the disk", actor="llm")
    assert hypothesis.origin == "llm"
    assert repo.change_hypothesis_status(hypothesis.id, "testing", actor="llm").status == "testing"
    observation = repo.add_observation(incident.id, "user_report", "Disk is almost full",
                                       "2026-11-07T09:59:00+00:00", "unknown", "user", actor="llm")
    evidence = repo.add_evidence(incident.id, "User says the disk is full", "observation",
                                 "stale", actor="llm", source_ref=observation.id)
    assert evidence.assessed_by == "llm"
    action = repo.plan_action(incident.id, "read_check", "get_disk_usage", actor="llm")
    assert (action.initiator, action.approval) == ("llm", "not_required")


# --- invariant 4: supported needs tool evidence or a user confirmation ---------------------


def test_4_rule_needs_supporting_tool_evidence(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")

    _, _, evidence = disk_check(repo, incident.id)
    repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="llm")
    assert repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule").status == "supported"


def test_4_evidence_from_an_observation_is_not_enough_for_a_rule(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    observation = repo.add_observation(incident.id, "user_report", "Disk is almost full",
                                       "2026-11-07T09:59:00+00:00", "unknown", "user", actor="user")
    evidence = repo.add_evidence(incident.id, "User report", "observation", "stale",
                                 actor="user", source_ref=observation.id)
    repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="user")

    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


def test_4_tool_evidence_from_another_incident_does_not_count(repo):
    other = investigating_incident(repo)
    _, _, foreign_evidence = disk_check(repo, other.id)
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    repo.link("evidence", foreign_evidence.id, "supports", "evidential", "hypothesis",
              hypothesis.id, actor="rule")

    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


def test_4_user_confirmation_needs_a_reason(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    for reason in (None, "", "   "):
        with pytest.raises(InvariantViolation, match="C9"):
            repo.change_hypothesis_status(hypothesis.id, "supported", actor="user", reason=reason)

    confirmed = repo.change_hypothesis_status(hypothesis.id, "supported", actor="user",
                                              reason="I removed old logs and the disk freed up")
    assert confirmed.status == "supported"
    event = repo.list_events("hypothesis", hypothesis.id)[-1]
    assert (event.actor, event.new_value, event.reason) == (
        "user", "supported", "I removed old logs and the disk freed up")


@pytest.mark.parametrize("actor", ["rule", "user"])
def test_4_an_unrefuted_contradiction_blocks_support(repo, actor):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, supporting = disk_check(repo, incident.id)
    _, _, contradicting = disk_check(repo, incident.id)
    repo.link("evidence", supporting.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="rule")
    contradiction = repo.link("evidence", contradicting.id, "contradicts", "evidential",
                              "hypothesis", hypothesis.id, actor="llm")

    with pytest.raises(InvariantViolation, match="contradicting"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor=actor, reason="ok")

    repo.change_relation_status(contradiction.id, "refuted", actor="rule")
    assert repo.change_hypothesis_status(hypothesis.id, "supported", actor=actor,
                                         reason="ok").status == "supported"


def test_4_a_refuted_supports_relation_does_not_count(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    relation = repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                         hypothesis.id, actor="llm")
    repo.change_relation_status(relation.id, "refuted", actor="rule")

    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


# --- invariant 5: a hypothesis from memory needs a new check (C10) -------------------------


@pytest.fixture
def memory_hypothesis(repo):
    incident = investigating_incident(repo)
    knowledge = repo.create_knowledge("root_cause_pattern", "Journal logs fill the disk",
                                      actor="rule", status="verified")
    hypothesis = hypothesis_in_testing(repo, incident.id, actor="rule",
                                    origin_knowledge_id=knowledge.id)
    assert hypothesis.origin == "memory"
    return incident, hypothesis


def test_5_new_check_of_this_incident_after_the_hypothesis_supports_it(repo, memory_hypothesis):
    incident, hypothesis = memory_hypothesis
    _, _, evidence = disk_check(repo, incident.id, hypothesis_id=hypothesis.id)
    repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="rule")

    assert repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule").status == "supported"


def test_5_a_tool_result_from_before_the_hypothesis_is_not_a_new_check(repo):
    incident = investigating_incident(repo)
    _, _, old_evidence = disk_check(repo, incident.id)
    knowledge = repo.create_knowledge("root_cause_pattern", "Journal logs fill the disk",
                                      actor="rule")
    hypothesis = hypothesis_in_testing(repo, incident.id, actor="rule",
                                    origin_knowledge_id=knowledge.id)
    repo.link("evidence", old_evidence.id, "supports", "evidential", "hypothesis",
              hypothesis.id, actor="rule")

    with pytest.raises(InvariantViolation, match="invariant 5"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


def test_5_a_tool_result_without_an_action_of_this_incident_is_not_a_new_check(
    repo, memory_hypothesis
):
    incident, hypothesis = memory_hypothesis
    loose = repo.record_tool_result(SUCCESS, actor="rule")  # no diagnostic action
    loose_evidence = repo.add_evidence(incident.id, "Disk usage is 89.3%", "tool_result",
                                       "fresh", actor="rule", source_ref=loose.id)
    other = investigating_incident(repo)
    _, foreign_result, _ = disk_check(repo, other.id)
    foreign_evidence = repo.add_evidence(incident.id, "Disk usage elsewhere", "tool_result",
                                         "fresh", actor="rule", source_ref=foreign_result.id)
    for evidence in (loose_evidence, foreign_evidence):
        repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                  hypothesis.id, actor="rule")

    with pytest.raises(InvariantViolation, match="invariant 5"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


def test_5_user_confirmation_alone_is_not_a_new_check(repo, memory_hypothesis):
    _, hypothesis = memory_hypothesis
    with pytest.raises(InvariantViolation, match="invariant 5"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="user",
                                      reason="I remember this happened before")


# --- invariant 6: failed knowledge is only a warning ----------------------------------------


def test_6_failed_knowledge_is_never_recommended(repo):
    created = {
        status: repo.create_knowledge("root_cause_pattern", f"Pattern {status}", actor="rule",
                                      status=status)
        for status in ("verified", "unverified", "failed")
    }
    deprecated = repo.create_knowledge("procedure", "Old procedure", actor="rule")
    repo.change_knowledge_status(deprecated.id, "deprecated", actor="rule",
                                 superseded_by=created["verified"].id)
    newly_failed = repo.change_knowledge_status(created["verified"].id, "failed", actor="rule")

    recommended = {item.id for item in repo.list_recommendable_knowledge()}
    warnings = {item.id for item in repo.list_knowledge_warnings()}
    assert recommended == {created["unverified"].id}
    assert warnings == {created["failed"].id, newly_failed.id}
    assert recommended.isdisjoint(warnings)


def test_6_deprecated_knowledge_is_skipped_by_retrieval_entirely(repo):
    """C4: deprecated is neither a recommendation nor a warning."""
    new = repo.create_knowledge("root_cause_pattern", "Journal logs fill the disk",
                                actor="rule", status="verified")
    for start in ("verified", "unverified", "failed"):
        old = repo.create_knowledge("root_cause_pattern", f"Old pattern ({start})",
                                    actor="rule", status=start)
        repo.change_knowledge_status(old.id, "deprecated", actor="rule", superseded_by=new.id)

    assert [item.id for item in repo.list_recommendable_knowledge()] == [new.id]
    assert repo.list_knowledge_warnings() == []


# --- invariant 7: every status change is journaled ------------------------------------------


def test_7_every_status_change_writes_one_event(repo, monkeypatch):
    from app.tools import registry

    monkeypatch.setitem(registry.TOOL_REGISTRY, "restart_service", lambda: None)
    monkeypatch.setitem(registry.TOOL_METADATA, "restart_service", {"operation": "action"})
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    action, _, evidence = disk_check(repo, incident.id, hypothesis_id=hypothesis.id)
    relation = repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis",
                         hypothesis.id, actor="llm")
    repo.change_relation_status(relation.id, "verified", actor="rule", reason="tool checked")
    repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule", reason="evidence")
    pending = repo.plan_action(incident.id, "action", "restart_service", actor="user")
    repo.change_action_approval(pending.id, "rejected", actor="user", reason="not now")
    knowledge = repo.create_knowledge("procedure", "Check the journal", actor="rule")
    repo.change_knowledge_status(knowledge.id, "verified", actor="rule")
    repo.change_incident_status(incident.id, "resolved", actor="user",
                                root_cause_hypothesis_id=hypothesis.id)

    changes = [(e.entity_type, e.old_value, e.new_value, e.actor)
               for e in repo.list_events() if e.event in ("status_changed", "reopened")]
    assert changes == [
        ("incident", "open", "investigating", "user"),
        ("hypothesis", "proposed", "testing", "llm"),
        ("diagnostic_action", "planned", "running", "rule"),
        ("diagnostic_action", "running", "succeeded", "rule"),
        ("memory_relation", "asserted", "verified", "rule"),
        ("hypothesis", "testing", "supported", "rule"),
        ("diagnostic_action", "approval:pending", "approval:rejected", "user"),
        ("knowledge", "unverified", "verified", "rule"),
        ("incident", "investigating", "resolved", "user"),
    ]
    assert action.id in {e.entity_id for e in repo.list_events("diagnostic_action")}
    created = [(e.entity_type, e.event) for e in repo.list_events()
               if e.event in ("created", "linked")]
    assert created == [
        ("incident", "created"),
        ("hypothesis", "created"),
        ("diagnostic_action", "created"),
        ("tool_result", "created"),
        ("evidence", "created"),
        ("memory_relation", "linked"),
        ("diagnostic_action", "created"),
        ("knowledge", "created"),
    ]


def test_7_rejected_change_leaves_status_and_journal_untouched(repo):
    incident = repo.open_incident("Disk almost full", "disk", actor="user")
    before = repo.list_events()

    with pytest.raises(InvalidTransition):
        repo.change_incident_status(incident.id, "resolved", actor="user")

    assert repo.get("incident", incident.id).status == "open"
    assert repo.list_events() == before


def test_7_status_is_rolled_back_when_the_event_cannot_be_written(repo, monkeypatch):
    incident = repo.open_incident("Disk almost full", "disk", actor="user")

    def broken_event(self, *args, **kwargs):
        raise RuntimeError("journal unavailable")

    # Only this patch is undone; the temporary JULI_DB_PATH stays in place.
    with monkeypatch.context() as patch:
        patch.setattr(MemoryRepository, "_event", broken_event)
        with pytest.raises(RuntimeError, match="journal unavailable"):
            repo.change_incident_status(incident.id, "investigating", actor="user")

    assert repo.get("incident", incident.id).status == "open"
    assert [e.event for e in repo.list_events("incident", incident.id)] == ["created"]


# --- C11: returning a hypothesis to testing ---------------------------------------------------


def supported_hypothesis(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    repo.link("evidence", evidence.id, "supports", "evidential", "hypothesis", hypothesis.id,
              actor="rule")
    return incident, repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")


def test_c11_rule_needs_new_evidence_to_reopen_a_hypothesis(repo):
    incident, hypothesis = supported_hypothesis(repo)
    with pytest.raises(InvariantViolation, match="C11"):
        repo.change_hypothesis_status(hypothesis.id, "testing", actor="rule")

    _, _, new_evidence = disk_check(repo, incident.id)
    repo.link("evidence", new_evidence.id, "contradicts", "evidential", "hypothesis",
              hypothesis.id, actor="llm")
    reopened = repo.change_hypothesis_status(hypothesis.id, "testing", actor="rule",
                                             reason="new contradicting evidence")
    assert reopened.status == "testing"
    assert repo.list_events("hypothesis", hypothesis.id)[-1].old_value == "supported"


def test_c11_user_may_reopen_and_llm_may_not(repo):
    _, hypothesis = supported_hypothesis(repo)
    with pytest.raises(ActorNotAllowed):
        repo.change_hypothesis_status(hypothesis.id, "testing", actor="llm")
    assert repo.change_hypothesis_status(hypothesis.id, "testing", actor="user").status == "testing"


# --- C14: root cause --------------------------------------------------------------------------


def test_c14_root_cause_must_be_a_supported_hypothesis_of_this_incident(repo):
    incident, supported = supported_hypothesis(repo)
    untested = repo.propose_hypothesis(incident.id, "Docker images fill the disk", actor="llm")
    other_incident, foreign = supported_hypothesis(repo)

    for candidate in (untested, foreign):
        with pytest.raises(InvariantViolation, match="C14"):
            repo.change_incident_status(incident.id, "resolved", actor="user",
                                        root_cause_hypothesis_id=candidate.id)
    with pytest.raises(InvariantViolation, match="resolving"):
        repo.change_incident_status(incident.id, "unresolved", actor="user",
                                    root_cause_hypothesis_id=supported.id)

    resolved = repo.change_incident_status(incident.id, "resolved", actor="user",
                                           root_cause_hypothesis_id=supported.id)
    assert resolved.root_cause_hypothesis_id == supported.id
    assert resolved.closed_at is not None


def test_c14_root_cause_cannot_leave_supported_until_the_incident_is_reopened(repo):
    incident, hypothesis = supported_hypothesis(repo)
    repo.change_incident_status(incident.id, "resolved", actor="user",
                                root_cause_hypothesis_id=hypothesis.id)
    with pytest.raises(InvariantViolation, match="reopen"):
        repo.change_hypothesis_status(hypothesis.id, "testing", actor="user")

    reopened = repo.change_incident_status(incident.id, "investigating", actor="user",
                                           reason="disk is full again")
    assert (reopened.status, reopened.closed_at, reopened.root_cause_hypothesis_id) == (
        "investigating", None, None)
    assert repo.list_events("incident", incident.id)[-1].event == "reopened"
    assert repo.change_hypothesis_status(hypothesis.id, "testing", actor="user").status == "testing"


# --- C15: supports / contradicts are evidential relations ------------------------------------


@pytest.mark.parametrize("relation", ["supports", "contradicts"])
@pytest.mark.parametrize("relation_class", ["associative", "semantic", "causal"])
def test_c15_supports_and_contradicts_must_be_evidential(repo, relation, relation_class):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)

    with pytest.raises(InvariantViolation, match="C15"):
        repo.link("evidence", evidence.id, relation, relation_class, "hypothesis",
                  hypothesis.id, actor="rule")
    assert repo.list_relations("hypothesis", hypothesis.id) == []
    assert repo.link("evidence", evidence.id, relation, "evidential", "hypothesis",
                     hypothesis.id, actor="rule").relation_class == "evidential"


def test_c15_a_semantic_relation_never_makes_a_hypothesis_supported(repo):
    incident = investigating_incident(repo)
    hypothesis = hypothesis_in_testing(repo, incident.id)
    _, _, evidence = disk_check(repo, incident.id)
    repo.link("evidence", evidence.id, "derived_from", "semantic", "hypothesis",
              hypothesis.id, actor="rule", status="verified")

    with pytest.raises(InvariantViolation, match="invariant 4"):
        repo.change_hypothesis_status(hypothesis.id, "supported", actor="rule")
