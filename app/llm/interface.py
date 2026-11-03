"""Provider-neutral messages and structured LLM results."""

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMError:
    code: str
    message: str


@dataclass(frozen=True)
class LLMUsage:
    """Token and timing counters reported by the provider; unknown values are None.

    ``thinking_chars`` is the length of the model's reasoning text. The text
    itself is never stored.
    """

    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration_ns: int | None = None
    done_reason: str | None = None
    thinking_chars: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "total_duration_ns": self.total_duration_ns,
            "done_reason": self.done_reason,
            "thinking_chars": self.thinking_chars,
        }


@dataclass(frozen=True)
class LLMResult:
    status: Literal["success", "error"]
    content: str | None
    model: str
    error: LLMError | None = None
    usage: LLMUsage | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "content": self.content,
            "model": self.model,
            "error": (
                {"code": self.error.code, "message": self.error.message}
                if self.error is not None
                else None
            ),
            "usage": self.usage.to_dict() if self.usage is not None else None,
        }


class LLMProvider(Protocol):
    """Small interface implemented by local model providers."""

    def generate(self, messages: list[ChatMessage]) -> LLMResult:
        """Generate a response to a list of role-tagged chat messages."""
