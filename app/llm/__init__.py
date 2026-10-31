"""Local language model interfaces and providers for JULI."""

from app.llm.config import OllamaConfig
from app.llm.interface import ChatMessage, LLMError, LLMProvider, LLMResult
from app.llm.ollama import OllamaProvider

__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "OllamaConfig",
    "OllamaProvider",
]
