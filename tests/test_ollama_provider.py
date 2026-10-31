import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from app.llm.config import OllamaConfig
from app.llm.ollama import OllamaProvider


def response(content="Local response", **extra):
    payload = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": content},
        "done": True,
        **extra,
    }
    return BytesIO(json.dumps(payload).encode("utf-8"))


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
    assert payload == {"model": "llama3.2", "messages": messages(), "stream": False}
    assert "tools" not in payload
    assert urlopen.call_args.kwargs["timeout"] == 120.0
    assert result.to_dict() == {
        "status": "success",
        "content": "Local response",
        "model": "llama3.2",
        "error": None,
    }


def test_provider_uses_environment_configuration(monkeypatch):
    monkeypatch.setenv("JULI_OLLAMA_HOST", "http://localhost:9999/")
    monkeypatch.setenv("JULI_OLLAMA_MODEL", "local-test")
    monkeypatch.setenv("JULI_OLLAMA_TIMEOUT_SECONDS", "7.5")
    config = OllamaConfig.from_environment()
    assert config.chat_url == "http://localhost:9999/api/chat"
    assert config.model == "local-test"
    assert config.timeout_seconds == 7.5


@pytest.mark.parametrize("config", [
    {"host": "localhost:11434"},
    {"host": "file:///tmp/ollama"},
    {"model": "  "},
    {"timeout_seconds": 0},
    {"timeout_seconds": float("inf")},
    {"timeout_seconds": True},
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
