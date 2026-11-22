"""Groq chat provider using the tested streaming and validation transport."""
import math
import re

from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.ai_analysis.providers.openrouter_provider import OpenRouterProvider


class GroqProvider(OpenRouterProvider):
    name = "groq"
    display_name = "Groq"
    service_name = "Groq API"
    api_url = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, settings, **kwargs):
        super().__init__(settings.model_copy(update={
            "openrouter_api_key": settings.groq_api_key,
            "openrouter_model": settings.groq_model,
            "openrouter_max_output_tokens": settings.groq_max_output_tokens,
            "openrouter_chunk_max_chars": settings.groq_chunk_max_chars,
            "openrouter_timeout_seconds": settings.groq_timeout_seconds,
            "openrouter_structured_outputs": False,
            "openrouter_reasoning": "default",
        }), **kwargs)

    def _headers(self):
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _body(self, request, max_tokens):
        body = super()._body(request, max_tokens)
        body.pop("usage", None)
        body.pop("reasoning", None)
        if self.model.startswith("openai/gpt-oss-"):
            body["reasoning_effort"] = "low"
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        # Reserve space within the free-tier request budget for the answer.
        estimated_input = math.ceil(len(request.system + request.user) / 3)
        available = 7500 - estimated_input
        if available < 512:
            raise LLMError("This request exceeds Groq's token budget. Consolidation must use smaller batches; saved findings are retained.", code="llm_request_too_large")
        body["max_completion_tokens"] = min(body.get("max_completion_tokens", 2500), request.max_tokens, available)
        return body

    def _server_wait(self, response, payload):
        seconds = super()._server_wait(response, payload)
        if seconds is not None:
            return seconds
        detail = str((payload.get("error") or {}).get("message", ""))
        match = re.search(r"try again in ((?:[0-9.]+(?:ms|[hms]))+)", detail)
        if match:
            units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
            return sum(float(n) * units[u] for n, u in re.findall(r"([0-9.]+)(ms|[hms])", match.group(1)))
        return None

    def _to_error(self, code, detail):
        if code in (401, 403, 404):
            return LLMConfigurationError("Groq credentials, model or project permissions are unavailable. Check GROQ_API_KEY and GROQ_MODEL.")
        if code == 413:
            return LLMError("Groq rejected an oversized request. Reduce the consolidation batch size; extracted findings are saved.", code="llm_request_too_large")
        if code == 429:
            daily = any(marker in detail.lower() for marker in ("per day", "per-day", "(tpd)", "(rpd)"))
            return LLMRateLimitError("Groq daily token quota reached." if daily else "Groq quota or rate limit reached.", code="llm_quota_exceeded" if daily else "llm_rate_limited")
        if code == 408:
            return LLMTimeoutError("Groq request timed out.")
        if code >= 500:
            return LLMUnavailableError("Groq is temporarily unavailable.")
        return LLMError("Groq rejected the request.", code="llm_bad_request")

    def _time_limit_error(self, reached, timeout_seconds=None):
        return LLMTimeoutError("Groq did not finish its response within the configured time limit. Retry or reduce GROQ_CHUNK_MAX_CHARS.")


    def _to_response(self, request, payload):
        try:
            return super()._to_response(request, payload)
        except LLMError as exc:
            if exc.code == "llm_output_truncated":
                raise LLMError("Groq's response reached its output budget before completion. Consolidation will use smaller batches where possible; saved findings are retained.", code=exc.code) from exc
            raise
