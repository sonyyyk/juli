"""Ollama chat API adapter using only Python's standard library."""

import json
import logging
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.llm.config import OllamaConfig
from app.llm.interface import ChatMessage, LLMError, LLMResult, LLMUsage


logger = logging.getLogger(__name__)
ALLOWED_ROLES = {"system", "user", "assistant"}
# Reasoning that a model or an older Ollama without a parser leaves inside content.
_THINK_BLOCK = re.compile(r"^\s*<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)
_THINKING_UNSUPPORTED = "does not support thinking"


class OllamaProvider:
    """Call a local Ollama `/api/chat` endpoint without tool execution."""

    def __init__(self, config: OllamaConfig | None = None) -> None:
        self.config = config or OllamaConfig.from_environment()

    def generate(self, messages: list[ChatMessage]) -> LLMResult:
        invalid = _validate_messages(messages)
        if invalid is not None:
            return self._error("INVALID_REQUEST", invalid)

        body = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "think": self.config.think,
            "options": self.config.options(),
        }).encode("utf-8")
        request = Request(
            self.config.chat_url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _read_error_detail(exc)
            if _THINKING_UNSUPPORTED in detail.lower():
                return self._thinking_unsupported()
            if exc.code == 404 or "model not found" in detail.lower():
                return self._error(
                    "MODEL_NOT_FOUND",
                    f"Ollama does not have model '{self.config.model}'. Pull or select an installed model.",
                )
            logger.warning("Ollama returned HTTP status %s", exc.code)
            return self._error("OLLAMA_HTTP_ERROR", f"Ollama returned HTTP status {exc.code}.")
        except (socket.timeout, TimeoutError):
            return self._error(
                "OLLAMA_TIMEOUT",
                f"Ollama did not respond within {self.config.timeout_seconds:g} seconds.",
            )
        except URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return self._error(
                    "OLLAMA_TIMEOUT",
                    f"Ollama did not respond within {self.config.timeout_seconds:g} seconds.",
                )
            logger.warning("Could not connect to the local Ollama endpoint")
            return self._error(
                "OLLAMA_CONNECTION_ERROR",
                "Could not connect to Ollama. Check that the local Ollama service is running.",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return self._error("INVALID_RESPONSE", "Ollama returned invalid JSON.")
        except OSError:
            logger.warning("Ollama connection failed", exc_info=True)
            return self._error(
                "OLLAMA_CONNECTION_ERROR",
                "Could not read a response from the local Ollama service.",
            )

        if not isinstance(payload, dict):
            return self._error("INVALID_RESPONSE", "Ollama response must be a JSON object.")
        if payload.get("error"):
            detail = str(payload["error"])
            if _THINKING_UNSUPPORTED in detail.lower():
                return self._thinking_unsupported()
            if "model not found" in detail.lower():
                return self._error(
                    "MODEL_NOT_FOUND",
                    f"Ollama does not have model '{self.config.model}'. Pull or select an installed model.",
                )
            return self._error("OLLAMA_ERROR", "Ollama reported an error while generating a response.")

        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            return self._error("INVALID_RESPONSE", "Ollama response is missing assistant message content.")
        if message.get("role", "assistant") != "assistant":
            return self._error("INVALID_RESPONSE", "Ollama returned a message with an unexpected role.")
        # Only message.content is the answer. message.thinking is never copied
        # into the result; only its length is kept as a cost metric.
        usage = _usage(payload, message)
        self._warn_if_context_full(usage)
        content = _THINK_BLOCK.sub("", message["content"], count=1).strip()
        if not content:
            return self._error("EMPTY_RESPONSE", "Ollama returned an empty response.", usage)

        model = payload.get("model", self.config.model)
        if not isinstance(model, str) or not model.strip():
            return self._error("INVALID_RESPONSE", "Ollama response contains an invalid model name.")
        return LLMResult(status="success", content=content, model=model, usage=usage)

    def _warn_if_context_full(self, usage: LLMUsage) -> None:
        used = (usage.prompt_eval_count or 0) + (usage.eval_count or 0)
        if usage.done_reason == "length" or used >= self.config.num_ctx:
            logger.warning(
                "Ollama context may be truncated: %d tokens used, num_ctx %d, done_reason %s",
                used,
                self.config.num_ctx,
                usage.done_reason,
            )

    def _thinking_unsupported(self) -> LLMResult:
        return self._error(
            "THINKING_NOT_SUPPORTED",
            f"Model '{self.config.model}' does not support thinking. Set JULI_OLLAMA_THINK=false.",
        )

    def _error(self, code: str, message: str, usage: LLMUsage | None = None) -> LLMResult:
        return LLMResult(
            status="error",
            content=None,
            model=self.config.model,
            error=LLMError(code=code, message=message),
            usage=usage,
        )


def _usage(payload: dict[str, Any], message: dict[str, Any]) -> LLMUsage:
    thinking = message.get("thinking")
    done_reason = payload.get("done_reason")
    return LLMUsage(
        prompt_eval_count=_count(payload.get("prompt_eval_count")),
        eval_count=_count(payload.get("eval_count")),
        total_duration_ns=_count(payload.get("total_duration")),
        done_reason=done_reason[:40] if isinstance(done_reason, str) else None,
        thinking_chars=len(thinking) if isinstance(thinking, str) else None,
    )


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _validate_messages(messages: Any) -> str | None:
    if not isinstance(messages, list) or not messages:
        return "Messages must be a non-empty list."
    has_user = False
    for message in messages:
        if not isinstance(message, dict):
            return "Each message must be a mapping."
        if message.get("role") not in ALLOWED_ROLES:
            return "Message role must be system, user, or assistant."
        if not isinstance(message.get("content"), str):
            return "Message content must be text."
        if not message["content"].strip():
            return "Message content must not be empty."
        if message["role"] == "user":
            has_user = True
    return None if has_user else "At least one user message is required."


def _read_error_detail(error: HTTPError) -> str:
    try:
        body = error.read(4096).decode("utf-8", errors="replace")
    except OSError:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(payload, dict):
        return str(payload.get("error", ""))
    return body
