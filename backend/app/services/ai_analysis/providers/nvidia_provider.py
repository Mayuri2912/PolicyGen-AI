"""NVIDIA hosted NIM chat API; prompts are validated by structured_call."""
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.ai_analysis.providers.openrouter_provider import OpenRouterProvider


class NvidiaProvider(OpenRouterProvider):
    name = "nvidia"
    display_name = "NVIDIA NIM"
    service_name = "NVIDIA NIM API"
    api_url = "https://integrate.api.nvidia.com/v1/chat/completions"

    def __init__(self, settings, **kwargs):
        super().__init__(settings.model_copy(update={
            "openrouter_api_key": settings.nvidia_api_key,
            "openrouter_model": settings.nvidia_model,
            "openrouter_max_output_tokens": min(4096, max(1, settings.nvidia_max_output_tokens)),
            "openrouter_chunk_max_chars": settings.nvidia_chunk_max_chars,
            "openrouter_timeout_seconds": settings.nvidia_timeout_seconds,
            "openrouter_reasoning": "default",
        }), **kwargs)
        self._format = "none"

    def _headers(self):
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "Accept": "text/event-stream"}

    def _body(self, request, max_tokens):
        body = super()._body(request, max_tokens)
        body.pop("usage", None)
        body.pop("reasoning", None)
        body.pop("response_format", None)
        body["max_tokens"] = min(4096, max_tokens or 4096, request.max_tokens)
        body["temperature"] = 0.2
        if self.model.startswith("nvidia/nemotron-3"):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        body["messages"][0]["content"] = "Return one valid JSON object only, matching the task schema.\n" + request.system
        return body

    def _to_error(self, code, detail):
        if code == 403 and any(s in detail.lower() for s in ("moderation", "content_filter", "policy violation")):
            return LLMRefusalError("NVIDIA declined to process this content.")
        if code in (401, 403, 404, 410):
            return LLMConfigurationError("NVIDIA credentials or model access are unavailable. Check NVIDIA_API_KEY and NVIDIA_MODEL.")
        if code == 402:
            return LLMRateLimitError("NVIDIA account quota or credits are exhausted.", code="llm_quota_exceeded")
        if code == 413 or (code in (400, 422) and any(s in detail for s in ("context length", "too large", "maximum context"))):
            return LLMError("NVIDIA rejected an oversized request. Use smaller batches; saved findings are retained.", code="llm_request_too_large")
        if code == 429:
            return LLMRateLimitError("NVIDIA quota or rate limit reached.")
        if code in (408, 504):
            return LLMTimeoutError("NVIDIA request timed out.")
        if code >= 500:
            return LLMUnavailableError("NVIDIA is temporarily unavailable.")
        return LLMError("NVIDIA rejected the request.", code="llm_bad_request")

    def _time_limit_error(self, reached, timeout_seconds=None):
        return LLMTimeoutError("NVIDIA did not complete its response within the configured time limit.")

    def _to_response(self, request, payload):
        try:
            return super()._to_response(request, payload)
        except LLMError as exc:
            if exc.code == "llm_output_truncated":
                raise LLMError("NVIDIA's response was cut off. Consolidation will try smaller batches where possible; saved work is retained.", code=exc.code) from exc
            raise
