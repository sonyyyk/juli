import json
import logging
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from app.llm.config import OllamaConfig
from app.llm.ollama import OllamaProvider


OLLAMA_ENV = (
    "JULI_OLLAMA_HOST",
    "JULI_OLLAMA_MODEL",
    "JULI_OLLAMA_TIMEOUT_SECONDS",
    "JULI_OLLAMA_THINK",
    "JULI_OLLAMA_NUM_CTX",
    "JULI_OLLAMA_TEMPERATURE",
    "JULI_OLLAMA_SEED",
)


@pytest.fixture(autouse=True)
def clean_ollama_env(monkeypatch):
    for name in OLLAMA_ENV:
        monkeypatch.delenv(name, raising=False)


def response(content="Local response", message_extra=None, **extra):
    payload = {
        "model": "qwen3.5:4b",
        "message": {"role": "assistant", "content": content, **(message_extra or {})},
        "done": True,
        **extra,
    }
    return BytesIO(json.dumps(payload).encode("utf-8"))


def generate(payload_stream, config=None):
    with patch("app.llm.ollama.urlopen", return_value=payload_stream) as urlopen:
        result = OllamaProvider(config or OllamaConfig()).generate(messages())
    return result, json.loads(urlopen.call_args.args[0].data.decode("utf-8"))


def messages():
    return [
        {"role": "system", "content": "Be accurate."},
        {"role": "user", "content": "Summarize current evidence."},
    ]


def test_ollama_chat_sends_context_and_returns_structured_result():
    with patch("app.llm.ollama.urlopen", return_value=response()) as urlopen:
        result = OllamaProvider(OllamaConfig()).generate(messages())

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert payload == {
        "model": "qwen3.5:4b",
        "messages": messages(),
        "stream": False,
        "think": False,
        "options": {"num_ctx": 16384, "temperature": 0.2},
    }
    assert "tools" not in payload
    assert urlopen.call_args.kwargs["timeout"] == 120.0
    assert result.to_dict() == {
        "status": "success",
        "content": "Local response",
        "model": "qwen3.5:4b",
        "error": None,
        "usage": {
            "prompt_eval_count": None,
            "eval_count": None,
            "total_duration_ns": None,
            "done_reason": None,
            "thinking_chars": None,
        },
    }


def test_think_true_and_seed_are_sent_when_configured():
    _, payload = generate(response(), OllamaConfig(think=True, seed=42, num_ctx=4096, temperature=0))
    assert payload["think"] is True
    assert payload["options"] == {"num_ctx": 4096, "temperature": 0, "seed": 42}


def test_provider_uses_environment_configuration(monkeypatch):
    monkeypatch.setenv("JULI_OLLAMA_HOST", "http://localhost:9999/")
    monkeypatch.setenv("JULI_OLLAMA_MODEL", "local-test")
    monkeypatch.setenv("JULI_OLLAMA_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("JULI_OLLAMA_THINK", " Yes ")
    monkeypatch.setenv("JULI_OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("JULI_OLLAMA_TEMPERATURE", "0.7")
    monkeypatch.setenv("JULI_OLLAMA_SEED", "7")
    config = OllamaConfig.from_environment()
    assert config.chat_url == "http://localhost:9999/api/chat"
    assert config.model == "local-test"
    assert config.timeout_seconds == 7.5
    assert config.think is True
    assert config.num_ctx == 8192
    assert config.temperature == 0.7
    assert config.seed == 7


def test_environment_defaults():
    config = OllamaConfig.from_environment()
    assert config.model == "qwen3.5:4b"
    assert config.think is False
    assert config.num_ctx == 16384
    assert config.temperature == 0.2
    assert config.seed is None


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("YES", True), ("false", False), ("0", False), ("No", False),
])
def test_think_environment_values(monkeypatch, raw, expected):
    monkeypatch.setenv("JULI_OLLAMA_THINK", raw)
    assert OllamaConfig.from_environment().think is expected


@pytest.mark.parametrize("name,raw", [
    ("JULI_OLLAMA_THINK", "maybe"),
    ("JULI_OLLAMA_THINK", ""),
    ("JULI_OLLAMA_NUM_CTX", "abc"),
    ("JULI_OLLAMA_NUM_CTX", "1024"),
    ("JULI_OLLAMA_NUM_CTX", "8192.5"),
    ("JULI_OLLAMA_TEMPERATURE", "nan"),
    ("JULI_OLLAMA_TEMPERATURE", "-0.1"),
    ("JULI_OLLAMA_TEMPERATURE", "2.5"),
    ("JULI_OLLAMA_SEED", "1.5"),
])
def test_invalid_environment_values_raise(monkeypatch, name, raw):
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match="JULI_OLLAMA"):
        OllamaConfig.from_environment()


def test_empty_seed_means_no_seed(monkeypatch):
    monkeypatch.setenv("JULI_OLLAMA_SEED", " ")
    assert OllamaConfig.from_environment().seed is None


@pytest.mark.parametrize("config", [
    {"host": "localhost:11434"},
    {"host": "file:///tmp/ollama"},
    {"model": "  "},
    {"timeout_seconds": 0},
    {"timeout_seconds": float("inf")},
    {"timeout_seconds": True},
    {"think": "false"},
    {"think": 0},
    {"num_ctx": 2047},
    {"num_ctx": True},
    {"num_ctx": 8192.0},
    {"temperature": float("nan")},
    {"temperature": 3},
    {"temperature": True},
    {"seed": 1.5},
    {"seed": True},
])
def test_invalid_provider_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        OllamaConfig(**config)


@pytest.mark.parametrize("case", [[], [{"role": "user"}], [{"role": "tool", "content": "x"}], [{"role": "system", "content": "x"}]])
def test_invalid_messages_return_structured_error_without_request(case):
    with patch("app.llm.ollama.urlopen") as urlopen:
        result = OllamaProvider().generate(case)
    assert result.status == "error"
    assert result.error.code == "INVALID_REQUEST"
    urlopen.assert_not_called()


def test_connection_error_is_controlled():
    with patch("app.llm.ollama.urlopen", side_effect=URLError(ConnectionRefusedError())):
        result = OllamaProvider().generate(messages())
    assert result.status == "error"
    assert result.error.code == "OLLAMA_CONNECTION_ERROR"


def test_timeout_is_controlled():
    with patch("app.llm.ollama.urlopen", side_effect=TimeoutError()):
        result = OllamaProvider().generate(messages())
    assert result.status == "error"
    assert result.error.code == "OLLAMA_TIMEOUT"


def test_missing_model_is_reported_from_http_error():
    error = HTTPError(
        "http://127.0.0.1:11434/api/chat", 404, "not found", {},
        BytesIO(b'{"error":"model not found"}'),
    )
    with patch("app.llm.ollama.urlopen", side_effect=error):
        result = OllamaProvider().generate(messages())
    assert result.status == "error"
    assert result.error.code == "MODEL_NOT_FOUND"


@pytest.mark.parametrize("payload,code", [
    (b"not json", "INVALID_RESPONSE"),
    (b"[]", "INVALID_RESPONSE"),
    (b'{"message":{"content":3}}', "INVALID_RESPONSE"),
    (b'{"message":{"role":"user","content":"hello"}}', "INVALID_RESPONSE"),
    (b'{"message":{"role":"assistant","content":"  "}}', "EMPTY_RESPONSE"),
    (b'{"error":"model not found: unavailable"}', "MODEL_NOT_FOUND"),
])
def test_malformed_and_empty_responses_are_controlled(payload, code):
    with patch("app.llm.ollama.urlopen", return_value=BytesIO(payload)):
        result = OllamaProvider().generate(messages())
    assert result.status == "error"
    assert result.error.code == code


def test_thinking_field_never_reaches_content_but_its_length_is_counted():
    secret = "SECRET-REASONING step by step"
    result, _ = generate(response("Final answer", message_extra={"thinking": secret}))
    assert result.content == "Final answer"
    assert result.usage.thinking_chars == len(secret)
    assert "SECRET-REASONING" not in json.dumps(result.to_dict())


def test_think_block_inside_content_is_removed():
    result, _ = generate(response("<think>\nSECRET-REASONING\n</think>\n\nFinal answer"))
    assert result.status == "success"
    assert result.content == "Final answer"


@pytest.mark.parametrize("content", ["<think>only reasoning</think>", "<think>unterminated reasoning"])
def test_content_with_only_reasoning_is_empty_response(content):
    result, _ = generate(response(content, message_extra={"thinking": "more reasoning"}))
    assert result.status == "error"
    assert result.error.code == "EMPTY_RESPONSE"
    assert result.content is None
    assert result.usage.thinking_chars == len("more reasoning")


def test_empty_content_does_not_fall_back_to_thinking():
    result, _ = generate(response("", message_extra={"thinking": "SECRET-REASONING"}))
    assert result.error.code == "EMPTY_RESPONSE"
    assert "SECRET-REASONING" not in json.dumps(result.to_dict())


def test_usage_is_parsed_from_response():
    result, _ = generate(response(
        prompt_eval_count=876, eval_count=567, total_duration=10_600_000_000, done_reason="stop",
    ))
    assert result.usage.to_dict() == {
        "prompt_eval_count": 876,
        "eval_count": 567,
        "total_duration_ns": 10_600_000_000,
        "done_reason": "stop",
        "thinking_chars": None,
    }


def test_invalid_usage_fields_become_none():
    result, _ = generate(response(
        message_extra={"thinking": 5},
        prompt_eval_count="876", eval_count=-1, total_duration=1.5, done_reason=3,
    ))
    assert result.status == "success"
    assert set(result.usage.to_dict().values()) == {None}


@pytest.mark.parametrize("extra", [
    {"prompt_eval_count": 16000, "eval_count": 384},
    {"prompt_eval_count": 10, "eval_count": 10, "done_reason": "length"},
])
def test_full_context_logs_warning(caplog, extra):
    with caplog.at_level(logging.WARNING, logger="app.llm.ollama"):
        result, _ = generate(response(**extra))
    assert result.status == "success"
    assert "context may be truncated" in caplog.text


def test_normal_usage_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="app.llm.ollama"):
        generate(response(prompt_eval_count=876, eval_count=567, done_reason="stop"))
    assert "truncated" not in caplog.text


def test_thinking_not_supported_from_http_error():
    error = HTTPError(
        "http://127.0.0.1:11434/api/chat", 400, "bad request", {},
        BytesIO(b'{"error":"\\"llama3.2\\" does not support thinking"}'),
    )
    with patch("app.llm.ollama.urlopen", side_effect=error):
        result = OllamaProvider(OllamaConfig(think=True)).generate(messages())
    assert result.error.code == "THINKING_NOT_SUPPORTED"
    assert "JULI_OLLAMA_THINK=false" in result.error.message


def test_thinking_not_supported_from_payload_error():
    with patch("app.llm.ollama.urlopen", return_value=BytesIO(b'{"error":"model does not support thinking"}')):
        result = OllamaProvider().generate(messages())
    assert result.error.code == "THINKING_NOT_SUPPORTED"
