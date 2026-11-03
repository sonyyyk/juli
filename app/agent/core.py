"""Evidence-guided Agent Core with bounded conversation context."""

from copy import deepcopy
import logging
from typing import Callable

from app.agent.context_builder import MIN_CONTEXT_CHARS, build_monitoring_context
from app.config import DEFAULT_MONITORING_CONTEXT_MAX_CHARS, get_monitoring_context_max_chars
from app.llm.config import OllamaConfig
from app.llm.interface import ChatMessage, LLMError, LLMProvider, LLMResult
from app.llm.ollama import OllamaProvider
from app.monitoring.analysis_service import run_monitoring_cycle


logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """You are JULI (Just Understanding Light Inside), a local Linux diagnostic assistant.
Use the supplied Monitoring context as evidence. Distinguish observed facts from hypotheses.
Missing or unavailable data is unknown, never zero. An anomaly is not automatically an incident.
Correlation or semantic similarity does not establish causality. PSI is additional context and is
not temporally linked to historical anomalies unless the evidence explicitly establishes that link.
You do not have shell access and cannot execute commands or actions. Do not claim that you changed
the system. Recommend read-only checks where useful; dangerous operations require human approval.
Explain uncertainty and suggest a concise diagnostic next step."""


class AgentCore:
    """Coordinate user conversation, Monitoring evidence, and an LLM provider.

    This core does not expose Tool Registry functions to model-generated calls.
    Monitoring Analysis is invoked through its fixed Python interface and its
    structured result is supplied as context to the local model.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        monitoring_provider: Callable[[], dict[str, object]] | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_turns: int = 12,
    ) -> None:
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty text")

        self.provider = provider or OllamaProvider(OllamaConfig.from_environment())
        self.monitoring_provider = monitoring_provider or run_monitoring_cycle
        self.system_prompt = system_prompt.strip()
        self.max_turns = max_turns
        self._history: list[ChatMessage] = []

    @property
    def history(self) -> list[ChatMessage]:
        """Return a copy of the successful user/assistant conversation turns."""
        return deepcopy(self._history)

    def clear_conversation(self) -> None:
        """Clear in-memory conversation context for the current UI session."""
        self._history.clear()

    def handle_message(
        self,
        user_message: str,
        monitoring_result: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Analyze one user message and return a structured agent result."""
        if not isinstance(user_message, str) or not user_message.strip():
            return {
                "status": "error",
                "response": None,
                "model": None,
                "error": {"code": "INVALID_MESSAGE", "message": "Message must be non-empty text."},
                "monitoring_status": None,
                "monitoring_issues": [],
            }

        monitoring_issue = None
        if monitoring_result is None:
            try:
                monitoring_result = self.monitoring_provider()
            except Exception as exc:  # Keep chat available when monitoring storage is unavailable.
                logger.warning("Monitoring context could not be loaded: %s", type(exc).__name__)
                monitoring_result = None
                monitoring_issue = {
                    "code": "MONITORING_UNAVAILABLE",
                    "message": "Monitoring evidence could not be loaded for this request.",
                }
        if monitoring_result is not None and not isinstance(monitoring_result, dict):
            monitoring_result = None
            monitoring_issue = {
                "code": "INVALID_MONITORING_RESULT",
                "message": "Monitoring returned an invalid result; system state is unknown.",
            }

        messages: list[ChatMessage] = [{"role": "system", "content": self.system_prompt}]
        if monitoring_result is not None:
            messages.append({
                "role": "system",
                "content": "Monitoring context (deterministic summary from JULI monitoring):\n"
                + build_monitoring_context(monitoring_result, _context_max_chars()),
            })
        elif monitoring_issue is not None:
            messages.append({
                "role": "system",
                "content": (
                    "Current Monitoring Analysis is unavailable. Treat system state as unknown; "
                    "do not infer values or assume zero."
                ),
            })
        messages.extend(deepcopy(self._history[-2 * self.max_turns:]))
        messages.append({"role": "user", "content": user_message.strip()})

        try:
            result = self.provider.generate(messages)
        except Exception:  # Providers should return LLMResult, but isolate adapters too.
            logger.exception("LLM provider raised an unexpected error")
            result = LLMResult(
                status="error",
                content=None,
                model="unknown",
                error=LLMError(
                    code="PROVIDER_ERROR",
                    message="The local language model provider failed.",
                ),
            )

        if not isinstance(result, LLMResult):
            return {
                "status": "error",
                "response": None,
                "model": None,
                "error": {"code": "INVALID_PROVIDER_RESULT", "message": "LLM provider returned an invalid result."},
                "monitoring_status": monitoring_result.get("status") if isinstance(monitoring_result, dict) else None,
                "monitoring_issues": _all_monitoring_issues(monitoring_result, monitoring_issue),
            }

        if result.status != "success" or not isinstance(result.content, str) or not result.content.strip():
            error = result.error
            return {
                "status": "error",
                "response": None,
                "model": result.model,
                "error": {
                    "code": error.code if error else "EMPTY_RESPONSE",
                    "message": error.message if error else "The local model returned no usable response.",
                },
                "monitoring_status": monitoring_result.get("status") if isinstance(monitoring_result, dict) else None,
                "monitoring_issues": _all_monitoring_issues(monitoring_result, monitoring_issue),
            }

        content = result.content.strip()
        self._history.extend([
            {"role": "user", "content": user_message.strip()},
            {"role": "assistant", "content": content},
        ])
        self._history = self._history[-2 * self.max_turns:]
        return {
            "status": "success",
            "response": content,
            "model": result.model,
            "error": None,
            "monitoring_status": monitoring_result.get("status") if isinstance(monitoring_result, dict) else None,
            "monitoring_issues": _all_monitoring_issues(monitoring_result, monitoring_issue),
        }


def _all_monitoring_issues(
    result: dict[str, object] | None,
    issue: dict[str, str] | None,
) -> list[dict[str, object]]:
    issues = _monitoring_issues(result)
    if issue is not None:
        issues.append(deepcopy(issue))
    return issues


def _monitoring_issues(result: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(result, dict):
        return []
    issues: list[dict[str, object]] = []
    for key in ("data_issues", "temporal_errors", "pressure_issues"):
        value = result.get(key)
        if isinstance(value, list):
            issues.extend(item for item in value if isinstance(item, dict))
    return deepcopy(issues)


def _context_max_chars() -> int:
    """Return the configured context limit, or the default when it is invalid."""
    try:
        value = get_monitoring_context_max_chars()
    except ValueError:
        value = None
    if value is None or value < MIN_CONTEXT_CHARS:
        logger.warning(
            "Invalid JULI_MONITORING_CONTEXT_MAX_CHARS (must be an integer >= %d); using %d",
            MIN_CONTEXT_CHARS,
            DEFAULT_MONITORING_CONTEXT_MAX_CHARS,
        )
        return DEFAULT_MONITORING_CONTEXT_MAX_CHARS
    return value
