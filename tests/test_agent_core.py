import logging

from app.agent.core import AgentCore
from app.llm.interface import LLMError, LLMResult
from app.models.observation import Observation


class FakeProvider:
    def __init__(self, result=None):
        self.result = result or LLMResult(
            status="success", content="Evidence is incomplete.", model="test-model"
        )
        self.calls = []

    def generate(self, messages):
        self.calls.append(messages)
        return self.result


def test_agent_builds_context_and_returns_structured_response():
    provider = FakeProvider()
    monitoring = {
        "status": "success",
        "snapshots_analyzed": 1,
        "data_issues": [],
        "pressure_observations": [
            Observation(
                "2026-10-11T10:00:00+00:00",
                "get_system_pressure",
                "psi_cpu_some_avg10",
                1.5,
                "%",
                {"resource": "cpu", "stall_type": "some", "window": "avg10"},
            )
        ],
    }
    agent = AgentCore(provider=provider, monitoring_provider=lambda: monitoring)

    result = agent.handle_message("What does this pressure value mean?")

    assert result["status"] == "success"
    assert result["response"] == "Evidence is incomplete."
    assert result["model"] == "test-model"
    assert result["monitoring_status"] == "success"
    assert provider.calls[0][0]["role"] == "system"
    evidence_message = provider.calls[0][1]
    assert evidence_message["role"] == "system"
    assert evidence_message["content"].startswith("Monitoring context")
    assert "- cpu some: avg10 1.50 / avg60 n/a / avg300 n/a %" in evidence_message["content"]
    assert "Current structured" not in evidence_message["content"]
    assert provider.calls[0][-1] == {
        "role": "user", "content": "What does this pressure value mean?"
    }
    assert agent.history == [
        {"role": "user", "content": "What does this pressure value mean?"},
        {"role": "assistant", "content": "Evidence is incomplete."},
    ]


def test_agent_supplies_conversation_context_and_bounds_stored_history():
    provider = FakeProvider()
    agent = AgentCore(provider=provider, monitoring_provider=lambda: {}, max_turns=1)
    agent.handle_message("first")
    agent.handle_message("second")
    sent_messages = provider.calls[-1]
    assert {"role": "user", "content": "first"} in sent_messages
    assert {"role": "assistant", "content": "Evidence is incomplete."} in sent_messages
    assert agent.history == [
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "Evidence is incomplete."},
    ]


def test_monitoring_failure_does_not_stop_local_chat():
    provider = FakeProvider()

    def unavailable_monitoring():
        raise OSError("private database details")

    agent = AgentCore(provider=provider, monitoring_provider=unavailable_monitoring)
    result = agent.handle_message("Explain the available data")

    assert result["status"] == "success"
    assert result["monitoring_status"] is None
    assert "unknown" in provider.calls[0][1]["content"].lower()
    assert "private database details" not in provider.calls[0][1]["content"]
    assert result["monitoring_issues"] == [{
        "code": "MONITORING_UNAVAILABLE",
        "message": "Monitoring evidence could not be loaded for this request.",
    }]


def test_failed_llm_result_is_controlled_and_not_added_to_history():
    provider = FakeProvider(LLMResult(
        status="error",
        content=None,
        model="test-model",
        error=LLMError("OLLAMA_TIMEOUT", "Timed out"),
    ))
    agent = AgentCore(provider=provider, monitoring_provider=lambda: {})
    result = agent.handle_message("Check the system")

    assert result["status"] == "error"
    assert result["error"] == {"code": "OLLAMA_TIMEOUT", "message": "Timed out"}
    assert agent.history == []


def test_empty_user_message_is_rejected_before_monitoring_or_llm():
    provider = FakeProvider()
    agent = AgentCore(provider=provider)
    result = agent.handle_message("  ")
    assert result["error"]["code"] == "INVALID_MESSAGE"
    assert provider.calls == []


def test_provider_exception_is_converted_to_structured_error():
    class RaisingProvider:
        def generate(self, messages):
            raise OSError("endpoint details")

    agent = AgentCore(provider=RaisingProvider(), monitoring_provider=lambda: {})
    result = agent.handle_message("Check this")
    assert result["status"] == "error"
    assert result["error"]["code"] == "PROVIDER_ERROR"
    assert "endpoint details" not in result["error"]["message"]


def test_invalid_provider_result_is_reported():
    class InvalidProvider:
        def generate(self, messages):
            return {"content": "not a typed result"}

    agent = AgentCore(provider=InvalidProvider(), monitoring_provider=lambda: {})
    result = agent.handle_message("Check this")
    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_PROVIDER_RESULT"


def test_agent_context_renders_non_finite_values_as_unknown():
    provider = FakeProvider()
    agent = AgentCore(provider=provider, monitoring_provider=lambda: {})
    result = agent.handle_message(
        "Review this",
        monitoring_result={
            "status": "partial",
            "current": {"timestamp": "2026-10-11T10:00:00+00:00", "cpu_usage": float("nan")},
        },
    )
    content = provider.calls[0][1]["content"]
    assert "CPU unknown" in content
    assert "nan" not in content.lower().replace("monitoring", "")
    assert result["status"] == "success"


def _large_monitoring_result():
    anomalies = [
        {
            "timestamp": f"2026-10-11T10:{minute:02d}:00+00:00",
            "metric": "cpu_usage",
            "value": 95.0,
            "unit": "%",
            "threshold": 90.0,
        }
        for minute in range(50)
    ]
    return {
        "status": "partial",
        "freshness": {"state": "stale"},
        "recent": {
            "window_minutes": 15,
            "window_start": "2026-10-11T09:00:00+00:00",
            "threshold_anomalies": anomalies,
            "significance_assessments": [{"anomaly": item} for item in anomalies],
        },
        "data_issues": [{"issue": f"issue_{index}"} for index in range(100)],
    }


def test_agent_context_respects_configured_limit(monkeypatch):
    monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", "1200")
    provider = FakeProvider()
    agent = AgentCore(provider=provider, monitoring_provider=_large_monitoring_result)
    agent.handle_message("Check this")
    header, context = provider.calls[0][1]["content"].split("\n", 1)
    assert header == "Monitoring context (deterministic summary from JULI monitoring):"
    assert len(context) <= 1200
    assert context.startswith("Data freshness: stale")


def test_invalid_context_limit_falls_back_to_default(monkeypatch, caplog):
    provider = FakeProvider()
    agent = AgentCore(provider=provider, monitoring_provider=_large_monitoring_result)
    for invalid in ("abc", "500"):
        monkeypatch.setenv("JULI_MONITORING_CONTEXT_MAX_CHARS", invalid)
        with caplog.at_level(logging.WARNING, logger="app.agent.core"):
            result = agent.handle_message("Check this")
        assert result["status"] == "success"
        context = provider.calls[-1][1]["content"].split("\n", 1)[1]
        assert 1200 < len(context) <= 4000
    assert "JULI_MONITORING_CONTEXT_MAX_CHARS" in caplog.text


def test_clear_conversation_removes_context():
    agent = AgentCore(provider=FakeProvider(), monitoring_provider=lambda: {})
    agent.handle_message("hello")
    agent.clear_conversation()
    assert agent.history == []
