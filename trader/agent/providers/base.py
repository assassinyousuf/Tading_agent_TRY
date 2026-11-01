"""Base LLM Provider abstraction and structured response container.

Enforces provider-neutral interface, structured schema parsing,
and deterministic cryptographic hashing of all inputs and outputs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel


@dataclass(frozen=True)
class LLMResponse:
    """Structured container for LLM provider completions and audit telemetry."""

    content: str
    structured_data: Optional[Dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    prompt_sha256: str
    response_sha256: str
    provider_name: str
    model_identifier: str


class LLMProvider(ABC):
    """Abstract base class for all LLM reasoning providers."""

    def __init__(self, name: str, model_identifier: str) -> None:
        self.name = name
        self.model_identifier = model_identifier

    @abstractmethod
    def generate(
        self,
        role: str,
        prompt: str,
        schema_class: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Generate a response conditioned on an agent role.

        Args:
            role: The agent execution role (e.g. Coordinator, Market Analyst, Skeptical Reviewer).
            prompt: Text prompt containing instructions and context.
            schema_class: Optional Pydantic model class for structured JSON validation.

        Returns:
            LLMResponse containing text, parsed structured dict, and audit metadata.
        """
        pass

    @staticmethod
    def compute_sha256(text: str) -> str:
        """Compute standard SHA-256 hex digest of string payload."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

