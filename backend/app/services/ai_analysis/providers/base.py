"""LLM provider contract.

The rest of the application talks to ``LLMProvider`` only.  Supporting another
model vendor means adding one subclass and registering it in ``factory.py``;
prompts, schemas, validation and the pipeline stay unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class LLMRequest:
    task: str  # extract_findings | consolidate | generate_policy | revise_sections
    system: str
    user: str
    schema: dict
    # The structured inputs the prompt was rendered from.  Hosted models use
    # ``system``/``user``; the offline mock provider works from this instead.
    payload: dict = field(default_factory=dict)
    max_tokens: int = 16000
    timeout_seconds: float | None = None
    # Called every few seconds while a provider waits for the model, with
    # ``{"model", "phase", "elapsed", "attempt"}``.  It lets a job show what
    # the model is doing, and stops the call by raising (a cancelled job).
    observer: Callable[[dict], None] | None = field(default=None, repr=False, compare=False)


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    simulated: bool = False


class LLMError(Exception):
    """A provider failure with a message that is safe to show to the user."""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        if code:
            self.code = code


class LLMConfigurationError(LLMError):
    code = "llm_not_configured"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"
    retryable = True


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"
    retryable = True


class LLMRefusalError(LLMError):
    code = "llm_refused"


class LLMInvalidResponseError(LLMError):
    code = "llm_invalid_response"


class LLMProvider(ABC):
    name: str = ""
    # How the provider is named in the UI and in generated documents.
    display_name: str = ""
    model: str = ""
    # The external service that receives document content, as shown to users in
    # the privacy notice ("Anthropic API", "Google Gemini API").  Empty for
    # providers that keep everything on this server.
    service_name: str = ""
    # True when results are produced by rules rather than a real model.  The
    # flag follows every analysis and policy so the UI and documents label them.
    simulated: bool = False
    # Largest document part this provider should be sent, when smaller than
    # CHUNK_MAX_CHARS.  None means no provider-specific limit.
    chunk_max_chars: int | None = None

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the model's raw text for ``request`` or raise ``LLMError``."""

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "display_name": self.display_name or self.name,
            "model": self.model,
            "simulated": self.simulated,
            "service_name": self.service_name,
        }
