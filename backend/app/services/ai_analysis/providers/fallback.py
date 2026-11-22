"""Bounded credential failover; completed pipeline chunks remain untouched."""
from __future__ import annotations

import math
import threading
import time

from app.core.logging import get_logger
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidResponseError,
    LLMProvider,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
)

logger = get_logger("llm.failover")


class FallbackProvider(LLMProvider):
    def __init__(self, providers: list[LLMProvider], cooldown: int = 60):
        self.providers = providers
        first = providers[0]
        for field in ("name", "display_name", "model", "service_name", "simulated", "chunk_max_chars"):
            setattr(self, field, getattr(first, field))
        limits = [p.chunk_max_chars for p in providers if p.chunk_max_chars is not None]
        self.chunk_max_chars = min(limits) if limits else None
        self.service_name = " / ".join(dict.fromkeys(p.service_name for p in providers))
        self._cooldown = max(1, cooldown)
        self._until = [0.0] * len(providers)
        self._reasons = [None] * len(providers)
        self._active = 0
        self._lock = threading.Lock()

    def describe(self) -> dict:
        with self._lock:
            states = []
            for index, candidate in enumerate(self.providers):
                delay = max(0, self._until[index] - time.monotonic())
                states.append({"provider": candidate.name, "model": candidate.model,
                    "key_slot": index + 1, "available": delay == 0,
                    "retry_after_seconds": None if math.isinf(delay) else math.ceil(delay),
                    "reason": self._reasons[index] if delay else None})
        return {**super().describe(), "configured_keys": len(self.providers), "key_status": states}

    def next_available_delay(self) -> float:
        with self._lock:
            return max(0.0, min(self._until) - time.monotonic())

    def _exhausted(self):
        delay = self.next_available_delay()
        if math.isinf(delay):
            return LLMConfigurationError("No configured API key is usable. Check the keys and model permissions; saved work is retained.")
        return LLMRateLimitError(
            f"All configured providers are unavailable or quota-limited. Next eligibility check in {math.ceil(delay)} seconds. Saved work is retained.",
            code="llm_providers_unavailable", retry_after_seconds=delay,
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            order = list(range(len(self.providers)))
            available = [i for i in order if self._until[i] <= time.monotonic()]
        if not available:
            raise self._exhausted()
        request_failure = None
        for index in available:
            try:
                candidate = self.providers[index]
                with self._lock:
                    if self._until[index] > time.monotonic():
                        continue
                logger.info("trying configured provider", extra={"provider": candidate.name, "model": candidate.model, "key_slot": index + 1, "task": request.task})
                if request.observer:
                    request.observer({"model": candidate.model, "provider": candidate.name, "phase": "waiting", "elapsed": 0, "attempt": available.index(index) + 1})
                response = candidate.complete(request)
                if not response.text.strip():
                    raise LLMInvalidResponseError("The AI provider returned an empty answer.")
            except LLMError as exc:
                # Refusals and bad requests must not be routed around.
                if not (exc.retryable or isinstance(exc, (LLMConfigurationError, LLMInvalidResponseError))
                        or exc.code in ("llm_empty_response", "llm_output_truncated", "llm_request_too_large")):
                    raise
                if exc.code in ("llm_output_truncated", "llm_request_too_large", "llm_invalid_response", "llm_empty_response"):
                    request_failure = exc
                # Request-specific failures do not exhaust a credential. Smaller
                # consolidation batches must be able to use it immediately.
                if exc.code not in ("llm_output_truncated", "llm_request_too_large", "llm_invalid_response", "llm_empty_response"):
                    delay = exc.retry_after_seconds
                    if isinstance(exc, LLMConfigurationError):
                        delay = float("inf")
                    elif exc.code == "llm_quota_exceeded":
                        delay = delay if delay is not None else 86400
                    elif isinstance(exc, LLMRateLimitError):
                        delay = max(1, delay) if delay is not None else self._cooldown
                    elif exc.code == "llm_timeout":
                        delay = max(300, self._cooldown)
                    else:
                        delay = max(1, delay) if delay is not None else min(5, self._cooldown)
                    with self._lock:
                        self._until[index] = time.monotonic() + delay
                        self._reasons[index] = exc.code
                    logger.warning("provider temporarily disabled", extra={"provider": candidate.name, "key_slot": index + 1, "code": exc.code, "retry_after_seconds": None if math.isinf(delay) else math.ceil(delay)})
                continue
            with self._lock:
                self._active = index
                self._reasons[index] = None
            return response
        if request_failure is not None:
            raise request_failure
        raise self._exhausted()

    def reject_response(self) -> None:
        """Try another credential after structured validation and repair fail."""
        with self._lock:
            self._until[self._active] = time.monotonic() + self._cooldown
            self._active = (self._active + 1) % len(self.providers)


class RetryingProvider(LLMProvider):
    """Retry the unfinished model step while retaining the same active job."""

    def __init__(self, provider: LLMProvider, retries: int = 2, delay: int = 5):
        self.provider = provider
        self.retries = max(0, min(3, retries))
        self.delay = max(1, min(30, delay))
        for field in ("name", "display_name", "model", "service_name", "simulated", "chunk_max_chars"):
            setattr(self, field, getattr(provider, field))

    def describe(self):
        return {**self.provider.describe(), "automatic_retries": self.retries}

    def reject_response(self):
        if hasattr(self.provider, "reject_response"):
            self.provider.reject_response()

    def complete(self, request: LLMRequest) -> LLMResponse:
        for attempt in range(self.retries + 1):
            try:
                if isinstance(self.provider, FallbackProvider):
                    return self.provider.complete(request)
                return self.provider.complete(request)
            except LLMError as exc:
                retryable = exc.retryable or isinstance(exc, LLMInvalidResponseError) or exc.code == "llm_empty_response"
                if not retryable or attempt == self.retries:
                    raise
                wait = self.delay * (attempt + 1)
                if isinstance(self.provider, FallbackProvider):
                    wait = max(wait, math.ceil(self.provider.next_available_delay())) if not math.isinf(self.provider.next_available_delay()) else float("inf")
                elif exc.code == "llm_quota_exceeded":
                    wait = max(wait, math.ceil(exc.retry_after_seconds or 86400))
                elif exc.retry_after_seconds is not None:
                    wait = max(wait, math.ceil(exc.retry_after_seconds))
                if wait > 60:
                    raise
                # Emit progress each second so cancellation remains responsive.
                for second in range(wait):
                    if request.observer:
                        request.observer({"model": self.model, "phase": "retrying", "elapsed": second, "attempt": attempt + 2})
                    time.sleep(1)
