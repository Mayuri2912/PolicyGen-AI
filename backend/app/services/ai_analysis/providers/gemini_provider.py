"""Google Gemini provider, built on the official ``google-genai`` SDK.

It answers the same four tasks as the other providers with the same prompts
and the same JSON schemas, so nothing downstream changes: the response still
goes through ``structured_call`` (parse, Pydantic validation, bounded repair).

The response is requested as JSON constrained to the task's schema
(``response_json_schema``).  If the API rejects the schema, the provider drops
the constraint once and relies on the prompt plus validation instead.  The API
key is read from server-side configuration and is sent only to Google.
"""

from __future__ import annotations

import httpx
from google import genai
from google.genai import errors, types

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

logger = get_logger("llm.gemini")

# Finish reasons that mean the model declined or filtered the content.
_BLOCKED_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class GeminiProvider(LLMProvider):
    name = "gemini"
    display_name = "Google Gemini"
    service_name = "Google Gemini API"
    simulated = False

    def __init__(self, settings: Settings, client: genai.Client | None = None):
        self.model = settings.gemini_model
        self._api_key = settings.gemini_api_key  # kept only to redact it from diagnostics
        self._max_output_tokens: int | None = settings.gemini_max_output_tokens
        self._structured = settings.gemini_structured_outputs
        try:
            self._client = client or genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=settings.llm_timeout_seconds * 1000,  # the SDK takes milliseconds
                    # Retries 408, 429 and 5xx with backoff; "attempts" includes the first try.
                    retry_options=types.HttpRetryOptions(attempts=settings.llm_max_retries + 1),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - any construction failure is a configuration problem
            raise LLMConfigurationError(
                "The Gemini client could not be created. Set GEMINI_API_KEY in backend/.env."
            ) from exc

    # ------------------------------------------------------------------ call
    def complete(self, request: LLMRequest) -> LLMResponse:
        # Two optional request features can be rejected by a model.  Each is
        # dropped at most once and the request retried, so this is bounded.
        for _ in range(3):
            try:
                response = self._send(request)
                break
            except errors.ClientError as exc:
                if self._degrade(exc):
                    continue
                raise self._client_error(exc) from exc
            except errors.ServerError as exc:
                logger.warning("gemini server error", extra={"status_code": exc.code, "status": exc.status, "detail": self._safe_message(exc)})
                if exc.code == 504 or exc.status == "DEADLINE_EXCEEDED":
                    raise LLMTimeoutError(
                        "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
                    ) from exc
                raise LLMUnavailableError("The AI provider is temporarily unavailable. Retry in a few minutes.") from exc
            except errors.APIError as exc:
                logger.error("gemini api error", extra={"status_code": exc.code, "status": exc.status, "detail": self._safe_message(exc)})
                raise LLMError("The AI provider returned an error for this request.", code="llm_api_error") from exc
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
                ) from exc
            except httpx.TransportError as exc:
                raise LLMUnavailableError(
                    "The AI provider could not be reached. Check the network connection and retry."
                ) from exc
        else:
            raise LLMError("The AI provider rejected the request.", code="llm_bad_request")

        return self._to_response(request, response)

    def _send(self, request: LLMRequest):
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            response_mime_type="application/json",
            response_json_schema=request.schema if self._structured else None,
            max_output_tokens=self._max_output_tokens,
            # No tools are used; switching this off also avoids an SDK warning on every call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return self._client.models.generate_content(model=self.model, contents=request.user, config=config)

    # ---------------------------------------------------------------- errors
    @staticmethod
    def _detail(exc: errors.APIError) -> str:
        """Lower-cased error text, used only to classify the failure (never shown or logged)."""
        return f"{exc.status} {exc.message} {exc.details}".lower()

    def _safe_message(self, exc: errors.APIError) -> str:
        """Google's explanation of the error for the server log, with the API key redacted."""
        message = (exc.message or "")[:300]
        return message.replace(self._api_key, "[redacted]") if self._api_key else message

    def _degrade(self, exc: errors.ClientError) -> bool:
        """Drop an optional feature the API rejected.  True when a retry is worthwhile."""
        if exc.code != 400:
            return False
        detail = self._detail(exc)
        if "api key" in detail or "api_key" in detail:
            return False
        if self._structured and any(k in detail for k in ("schema", "too many states", "response_json")):
            logger.warning("gemini rejected the response schema; continuing with prompt-enforced JSON")
            self._structured = False
            return True
        if self._max_output_tokens is not None and any(k in detail for k in ("max_output_tokens", "maxoutputtokens", "max output tokens")):
            logger.warning("gemini rejected the output-token limit; continuing with the model default")
            self._max_output_tokens = None
            return True
        return False

    def _client_error(self, exc: errors.ClientError) -> LLMError:
        detail = self._detail(exc)
        logger.error(
            "gemini rejected the request",
            extra={"status_code": exc.code, "status": exc.status, "detail": self._safe_message(exc)},
        )
        invalid_key = "api key" in detail or "api_key" in detail
        if exc.code in (401, 403) or invalid_key:
            return LLMConfigurationError(
                "The Gemini API key was rejected, or it is not allowed to use this model. Check GEMINI_API_KEY in backend/.env."
            )
        if exc.code == 404 or "is not found" in detail or "not supported for generatecontent" in detail:
            return LLMConfigurationError(
                f"The model '{self.model}' is not available to this API key (it may have been retired) or does not support "
                "text generation. Set GEMINI_MODEL in backend/.env to a current model, for example gemini-3.8-flash."
            )
        if exc.code == 429:
            return LLMRateLimitError(
                "The Gemini quota or rate limit was reached and retries were exhausted. Wait a minute and retry, "
                "or check the quota of your Google AI project."
            )
        if exc.code == 408:
            return LLMTimeoutError(
                "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
            )
        return LLMError(
            "The AI provider rejected the request. The document may be too large for one request; "
            "lower CHUNK_MAX_CHARS and try again.",
            code="llm_bad_request",
        )

    # -------------------------------------------------------------- response
    def _to_response(self, request: LLMRequest, response) -> LLMResponse:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMRefusalError("The AI provider declined to process this content.")
        candidates = response.candidates or []
        if not candidates:
            raise LLMError("The AI provider returned an empty response. Retry the operation.", code="llm_empty_response")

        candidate = candidates[0]
        finish = getattr(candidate.finish_reason, "name", None) or str(candidate.finish_reason or "")
        if finish in _BLOCKED_FINISH:
            raise LLMRefusalError("The AI provider declined to process this content.")
        if finish == "MAX_TOKENS":
            raise LLMError(
                "The AI response was cut off at the output limit. Raise GEMINI_MAX_OUTPUT_TOKENS or lower "
                "CHUNK_MAX_CHARS and retry.",
                code="llm_output_truncated",
            )

        parts = (candidate.content.parts if candidate.content else None) or []
        # Thought summaries are not part of the answer.
        text = "".join(part.text for part in parts if getattr(part, "text", None) and not getattr(part, "thought", False))
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        logger.info(
            "gemini call completed",
            extra={
                "task": request.task,
                "model": getattr(response, "model_version", None) or self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return LLMResponse(
            text=text,
            model=getattr(response, "model_version", None) or self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
