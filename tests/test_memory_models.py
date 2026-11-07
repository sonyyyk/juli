"""Field validation of the memory dataclasses (section 2, C2, C12)."""

import math
import re

import pytest

from app.memory.ids import ID_PREFIXES, has_prefix, new_id
from app.memory.models import (
    Evidence,
    Hypothesis,
    Incident,
    IncidentObservation,
    Knowledge,
    MemoryRelation,
    StoredToolResult,
    parse_utc,
)
from app.models.observation import Observation


AT = "2026-11-07T10:00:00+00:00"


def observation(**changes):
    values = dict(id=new_id("observation"), incident_id=new_id("incident"), kind="metric",
                  statement="disk usage 89.3%", observed_at=AT, freshness="fresh",
                  source_type="snapshot", source_ref="1393", resource="disk",
                  metric="usage_percent", value=89.3, unit="%")
    return IncidentObservation(**{**values, **changes})


def hypothesis(**changes):
    values = dict(id=new_id("hypothesis"), incident_id=new_id("incident"),
                  statement="Disk fills up due to growing journal logs", origin="llm",
                  status="proposed", created_at=AT, updated_at=AT)
    return Hypothesis(**{**values, **changes})


def test_ids_have_the_entity_prefix_and_are_unique():
    for entity_type, prefix in ID_PREFIXES.items():
        value = new_id(entity_type)
        assert re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", value)
        assert has_prefix(entity_type, value)
    assert len({new_id("incident") for _ in range(100)}) == 100
    with pytest.raises(ValueError):
        new_id("unknown")


def test_memory_observation_is_separate_from_the_monitoring_observation():
    assert IncidentObservation is not Observation
    assert observation().statement == "disk usage 89.3%"


@pytest.mark.parametrize("statement", ["диск заповнений на 89%", "Disk is повний", "Їжак"])
def test_statement_must_be_english(statement):
    with pytest.raises(ValueError, match="English"):
        observation(statement=statement)
    with pytest.raises(ValueError, match="English"):
        hypothesis(statement=statement)
    with pytest.raises(ValueError, match="English"):
        Knowledge(id=new_id("knowledge"), kind="procedure", statement=statement,
                  status="unverified", created_at=AT, updated_at=AT)


def test_other_text_fields_accept_any_language():
    incident = Incident(id=new_id("incident"), title="Диск", symptom_text="Диск майже повний",
                        status="open", opened_by="user", host_context={}, created_at=AT,
                        updated_at=AT)
    evidence = Evidence(id=new_id("evidence"), incident_id=incident.id,
                        summary="Користувач каже, що диск повний", source_type="user",
                        quality="stale", assessed_by="user", created_at=AT)
    assert incident.symptom_text and evidence.summary


@pytest.mark.parametrize("value", ["2026-11-07T10:00:00", "2026-11-07T12:00:00+02:00",
                                   "07.11.2026", "", None, 1730973600])
def test_timestamps_must_be_iso_utc(value):
    assert parse_utc(value) is None
    with pytest.raises(ValueError, match="UTC"):
        observation(observed_at=value)


@pytest.mark.parametrize("field,value", [
    ("kind", "guess"), ("freshness", "recent"), ("source_type", "llm"),
])
def test_unknown_enum_values_are_rejected(field, value):
    with pytest.raises(ValueError, match="must be one of"):
        observation(**{field: value})


@pytest.mark.parametrize("value", [math.nan, math.inf, True, "89.3"])
def test_values_must_be_finite_numbers(value):
    with pytest.raises(ValueError, match="finite"):
        observation(value=value)


def test_snapshot_and_tool_result_observations_need_a_source_ref():
    with pytest.raises(ValueError, match="source_ref"):
        observation(source_ref=None)
    assert observation(source_type="user", source_ref=None).source_ref is None


def test_memory_origin_and_knowledge_id_go_together():
    with pytest.raises(ValueError, match="origin_knowledge_id"):
        hypothesis(origin="memory")
    with pytest.raises(ValueError, match="origin_knowledge_id"):
        hypothesis(origin="llm", origin_knowledge_id=new_id("knowledge"))
    assert hypothesis(origin="memory", origin_knowledge_id=new_id("knowledge")).origin == "memory"


def test_hypothesis_has_no_numeric_confidence_field():
    assert not any("confidence" in name for name in Hypothesis.__dataclass_fields__)


def test_tool_result_shapes_follow_tool_result_rules():
    base = dict(id=new_id("tool_result"), tool_name="get_disk_usage", parameters={}, created_at=AT)
    with pytest.raises(ValueError, match="no payload"):
        StoredToolResult(status="error", payload={"x": 1}, **base)
    with pytest.raises(ValueError, match="no error"):
        StoredToolResult(status="success", error={"code": "X", "message": "m"}, **base)
    with pytest.raises(ValueError, match="JSON"):
        StoredToolResult(status="success", payload={"x": math.nan}, **base)
    partial = StoredToolResult(status="partial", payload={"cpu": None},
                               error={"code": "SYSTEM_STATE_PARTIAL", "message": "m"}, **base)
    assert partial.error["code"] == "SYSTEM_STATE_PARTIAL"


def test_evidence_source_ref_must_match_its_source_type():
    base = dict(id=new_id("evidence"), incident_id=new_id("incident"), summary="s",
                quality="fresh", assessed_by="rule", created_at=AT)
    with pytest.raises(ValueError, match="tool_result id"):
        Evidence(source_type="tool_result", source_ref=new_id("observation"), **base)
    with pytest.raises(ValueError, match="no source_ref"):
        Evidence(source_type="user", source_ref="x", **base)


def test_relation_endpoints_must_match_their_types():
    with pytest.raises(ValueError, match="evidence id"):
        MemoryRelation(id=new_id("memory_relation"), src_type="evidence",
                       src_id=new_id("hypothesis"), relation="supports",
                       relation_class="evidential", dst_type="hypothesis",
                       dst_id=new_id("hypothesis"), status="asserted", created_by="llm",
                       created_at=AT)


def test_knowledge_counts_and_self_supersession_are_checked():
    base = dict(kind="procedure", statement="Check the journal size", status="deprecated",
                created_at=AT, updated_at=AT)
    knowledge_id = new_id("knowledge")
    with pytest.raises(ValueError, match="supersede itself"):
        Knowledge(id=knowledge_id, superseded_by=knowledge_id, **base)
    with pytest.raises(ValueError, match="non-negative"):
        Knowledge(id=new_id("knowledge"), times_confirmed=-1, **base)
    with pytest.raises(ValueError, match="incident id"):
        Knowledge(id=new_id("knowledge"), source_incident_ids=["hyp_1"], **base)


def test_rows_round_trip_with_json_fields():
    incident = Incident(id=new_id("incident"), title="t", symptom_text="s", status="open",
                        opened_by="juli_suggested", host_context={"hostname": "box", "kernel": "7.0"},
                        created_at=AT, updated_at=AT)
    row = incident.to_row()
    assert row["host_context"] == '{"hostname": "box", "kernel": "7.0"}'
    assert Incident.from_row({**row}) == incident
