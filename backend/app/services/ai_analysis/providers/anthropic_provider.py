"""Anthropic Claude provider (the primary LLM).

Uses the official SDK with streaming (long analyses and policies would
otherwise risk HTTP timeouts), adaptive thinking, and structured outputs so the
response is constrained to the task's JSON schema.  The API key is read from
server-side configuration and never leaves the backend.
"""

from __future__ import annotations

import anthropic

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = get_logger("llm.anthropic")

# Lets the API re-run a request on Anthropic's recommended fallback model when
# a safety classifier declines it.  Security assessments discuss attacks and
# weaknesses, so a false positive is plausible; without this the job would fail.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    display_name = "Anthropic Claude"
    service_name = "Anthropic API"
    simulated = False

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.model = settings.anthropic_model
        self._effort = settings.anthropic_effort
        self._max_output_tokens = settings.anthropic_max_output_tokens
        self._structured = settings.anthropic_structured_outputs
        self._fallback = settings.anthropic_refusal_fallback
        try:
            self._client = client or anthropic.Anthropic(
                api_key=settings.anthropic_api_key or None,
                timeout=float(settings.llm_timeout_seconds),
                max_retries=settings.llm_max_retries,  # SDK retries 408/409/429/5xx with backoff
            )
        except anthropic.AnthropicError as exc:
            raise LLMConfigurationError(
                "The Anthropic client could not be created. Set ANTHROPIC_API_KEY in backend/.env."
            ) from exc

    # ------------------------------------------------------------------ call
    def complete(self, request: LLMRequest) -> LLMResponse:
        # Two optional features can be rejected by an account or model.  Each
        # is dropped at most once and the request is retried, so the loop is
        # bounded at three attempts.
        for _ in range(3):
            try:
                message = self._send(request)
                break
            except anthropic.BadRequestError as exc:
                detail = (getattr(exc, "message", "") or "").lower()
                if self._fallback and ("anthropic-beta" in detail or "fallback" in detail):
                    logger.warning("refusal fallback not accepted; continuing without it")
                    self._fallback = False
                elif self._structured and any(k in detail for k in ("output_config", "schema", "format")):
                    logger.warning("structured outputs not accepted; continuing with prompt-enforced JSON")
                    self._structured = False
                else:
                    logger.error("anthropic rejected the request", extra={"request_id": getattr(exc, "request_id", None)})
                    raise LLMError(
                        "The AI provider rejected the request. The document may be too large for one request; "
                        "lower CHUNK_MAX_CHARS and try again.",
                        code="llm_bad_request",
                    ) from exc
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
                raise LLMConfigurationError(
                    "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in backend/.env."
                ) from exc
            except anthropic.NotFoundError as exc:
                raise LLMConfigurationError(
                    f"The model '{self.model}' is not available to this API key. Check ANTHROPIC_MODEL."
                ) from exc
            except anthropic.RateLimitError as exc:
                raise LLMRateLimitError(
                    "The AI provider's rate limit was reached and retries were exhausted. Wait a minute and retry."
                ) from exc
            except anthropic.APITimeoutError as exc:
                raise LLMTimeoutError(
                    "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise LLMUnavailableError(
                    "The AI provider could not be reached. Check the network connection and retry."
                ) from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    raise LLMUnavailableError(
                        "The AI provider is temporarily unavailable. Retry in a few minutes."
                    ) from exc
                raise LLMError("The AI provider returned an error for this request.", code="llm_api_error") from exc
        else:
            raise LLMError("The AI provider rejected the request.", code="llm_bad_request")

        if message.stop_reason == "refusal":
            raise LLMRefusalError("The AI provider declined to process this content.")
        if message.stop_reason == "max_tokens":
            raise LLMError(
                "The AI response was cut off at the output limit. Raise ANTHROPIC_MAX_OUTPUT_TOKENS or lower "
                "CHUNK_MAX_CHARS and retry.",
                code="llm_output_truncated",
            )

        text = "".join(block.text for block in message.content if block.type == "text")
        logger.info(
            "anthropic call completed",
            extra={
                "task": request.task,
                "model": message.model,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        )
        return LLMResponse(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

    def _send(self, request: LLMRequest):
        output_config: dict = {"effort": self._effort}
        if self._structured:
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        params = {
            "model": self.model,
            "max_tokens": min(request.max_tokens, self._max_output_tokens),
            # The system prompt is identical across the calls of one job, so it
            # is marked cacheable.
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": request.user}],
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
        }
        if self._fallback:
            stream_manager = self._client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            stream_manager = self._client.messages.stream(**params)
        with stream_manager as stream:
            return stream.get_final_message()
