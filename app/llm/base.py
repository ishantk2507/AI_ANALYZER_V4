"""The contract every inference backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict


class ChatMessage(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class BackendUnavailable(RuntimeError):
    """Raised when a backend cannot be reached or initialised."""


@dataclass
class BackendInfo:
    name: str
    model: str
    gpu: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        # ASCII only: this is printed to Windows consoles that are still cp1252.
        where = "GPU" if self.gpu else "CPU"
        return f"{self.name} | {self.model} | {where}"


class LLMBackend(ABC):
    """A minimal chat interface with optional schema-constrained decoding.

    `schema` is a JSON Schema dict. When supplied, the backend must constrain
    decoding so the returned string is valid JSON conforming to it. This is the
    whole reason a 3B model can drive this agent reliably: it never gets the
    chance to emit a malformed tool call.
    """

    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> str:
        """`model` overrides the backend's default for this call, where the
        backend can do that. Used to send one stage to a larger model."""
        ...

    @abstractmethod
    def info(self) -> BackendInfo:
        ...

    def close(self) -> None:  # pragma: no cover - most backends need nothing
        return None
