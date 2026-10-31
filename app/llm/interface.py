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
class LLMResult:
    status: Literal["success", "error"]
    content: str | None
    model: str
    error: LLMError | None = None

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
        }


class LLMProvider(Protocol):
    """Small interface implemented by local model providers."""

    def generate(self, messages: list[ChatMessage]) -> LLMResult:
        """Generate a response to a list of role-tagged chat messages."""
