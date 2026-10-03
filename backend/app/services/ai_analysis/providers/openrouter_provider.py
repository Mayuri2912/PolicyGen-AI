"""OpenRouter provider.

Calls ``https://openrouter.ai/api/v1/chat/completions`` (the OpenAI-compatible
chat endpoint) with ``httpx`` and Bearer authentication.  It answers the same
four tasks as the other providers with the same prompts and JSON schemas, and
its response goes through the same ``structured_call`` validation and repair.

OpenRouter fronts many models and they differ in what they accept, free ones
especially.  The response format is therefore requested in the strongest form
first and stepped down only when the API says the model cannot do it:

    json_schema (the task's schema, strict)  ->  json_object  ->  no constraint

The answer is read as a stream under one total time limit.  OpenRouter keeps
a waiting connection alive with a few bytes every few seconds, so a read
timeout alone never ends a request that a model does not finish; without the
total limit such a request, and the job behind it, would wait for ever.  The
stream also shows what the model is doing (reasoning or writing), which is
passed to the request's observer for the job's progress message.

Free models are mostly reasoning models.  Left alone they can think for many
minutes before writing a first character, so reasoning is switched off unless
OPENROUTER_REASONING says otherwise.

Free models are limited per minute and per day.  A per-minute limit is waited
out (bounded); the daily limit and a missing credit balance are permanent for
the day, so they are reported at once instead of being retried.

The API key is read from server-side configuration, sent only in the
``Authorization`` header to OpenRouter, and redacted from anything logged.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable

import httpx

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

logger = get_logger("llm.openrouter")

API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Worth retrying: timeouts, rate limits, upstream model or provider trouble.
_TRANSIENT = {408, 429, 500, 502, 503, 504, 524, 529}
_FORMAT_STEPS = ("json_schema", "json_object", "none")
_FORMAT_HINTS = (
    "response_format", "json_schema", "json schema", "structured output", "structured_output", "json mode",
    "json_object", "support the requested parameters", "no endpoints found that support", "schema",
)
_TOKEN_HINTS = ("max_tokens", "max tokens", "maximum context length", "context length", "max_completion_tokens")
_DAILY_LIMIT_HINTS = ("per-day", "per day", "daily")
_REASONING = {"off": {"enabled": False}, "low": {"effort": "low"}, "medium": {"effort": "medium"}, "high": {"effort": "high"}}
# Silence on the connection for this long means it is dead (keep-alives arrive every few seconds).
_READ_TIMEOUT_SECONDS = 90.0
_REPORT_EVERY_SECONDS = 5.0
# Once a model has started writing its answer, text keeps arriving.  A gap this
# long means the host stalled; waiting for the total limit would gain nothing.
# (Hosts that deliver the whole answer at once never start, so are not affected.)
_STALL_SECONDS = 90.0
_MAX_BACKOFF_SECONDS = 16.0
# A per-minute limit says when it resets; waiting that long is worthwhile.
_MAX_WAIT_SECONDS = 60.0
# Reasoning models may put their thinking in front of the answer.
_THINKING = re.compile(r"^\s*<think(?:ing)?>.*?</think(?:ing)?>\s*", re.DOTALL | re.IGNORECASE)


def inline_refs(schema: dict) -> dict:
    """Replace ``$ref`` pointers with the definitions they name.

    Pydantic emits ``$defs``/``$ref``.  Several of the hosts behind OpenRouter
    accept only self-contained schemas, so the references are expanded.  A
    recursive or dangling reference leaves the schema as it was written.
    """
    definitions = schema.get("$defs") or {}
    if not definitions:
        return schema

    def resolve(node, trail: tuple = ()):
        if isinstance(node, list):
            return [resolve(item, trail) for item in node]
        if not isinstance(node, dict):
            return node
        rest = {key: resolve(value, trail) for key, value in node.items() if key not in ("$ref", "$defs")}
        ref = node.get("$ref")
        if not isinstance(ref, str):
            return rest
        name = ref.removeprefix("#/$defs/")
        if name in trail or name not in definitions:
            raise ValueError(name)
        return {**resolve(definitions[name], (*trail, name)), **rest}

    try:
        return resolve(schema)
    except ValueError:
        return schema


class _TimeLimitReached(Exception):
    """The total time limit passed while the answer was still arriving."""

    def __init__(self, seconds: float, state: dict, stalled: bool = False):
        super().__init__("time limit reached")
        self.seconds = seconds
        self.state = state
        self.stalled = stalled


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    display_name = "OpenRouter"
    service_name = "OpenRouter API"
    simulated = False

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.model = settings.openrouter_model
        self._api_key = settings.openrouter_api_key
        self._max_output_tokens: int | None = settings.openrouter_max_output_tokens or None
        self._format = "json_schema" if settings.openrouter_structured_outputs else "json_object"
        self._reasoning: dict | None = _REASONING.get(settings.openrouter_reasoning)
        self.chunk_max_chars = settings.openrouter_chunk_max_chars or None
        self._max_retries = max(0, settings.llm_max_retries)
        self._timeout = float(settings.openrouter_timeout_seconds or settings.llm_timeout_seconds)
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(min(self._timeout, _READ_TIMEOUT_SECONDS), connect=15.0)
        )

    # ------------------------------------------------------------------ call
    def complete(self, request: LLMRequest) -> LLMResponse:
        retries_left = self._max_retries
        attempt = 0
        limit_lifted = False
        timed_out = False
        # Bounded: at most LLM_MAX_RETRIES transient retries (only one of them
        # for a request that ran into the total time limit), plus at most five
        # one-off adjustments (reasoning setting, two format steps, one
        # token-limit drop, one retry of a truncated answer from a free model).
        while True:
            attempt += 1
            limit = None if limit_lifted else self._max_output_tokens
            body = self._body(request, limit)
            logger.info(
                "openrouter call started",
                extra={
                    "task": request.task, "model": self.model, "attempt": attempt, "response_format": self._format,
                    "prompt_chars": len(request.system) + len(request.user), "max_tokens": limit or 0,
                    "time_limit_seconds": int(self._timeout),
                },
            )
            try:
                response, payload = self._send(request, body, attempt)
            except _TimeLimitReached as exc:
                state = exc.state
                logger.warning(
                    "openrouter request reached the time limit",
                    extra={
                        "task": request.task, "model": state["model"] or self.model, "attempt": attempt,
                        "seconds": int(exc.seconds), "stalled": exc.stalled, "reasoning_chars": state["reasoning_chars"],
                        "answer_chars": state["answer_chars"],
                    },
                )
                # A second try often lands on another model or a less busy host; more would only repeat the wait.
                if retries_left > 0 and not timed_out:
                    timed_out = True
                    retries_left -= 1
                    continue
                raise self._time_limit_error(exc) from None
            except httpx.TimeoutException as exc:
                if retries_left > 0:
                    retries_left -= 1
                    self._wait(attempt)
                    continue
                raise LLMTimeoutError(
                    "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
                ) from exc
            except httpx.TransportError as exc:
                if retries_left > 0:
                    retries_left -= 1
                    self._wait(attempt)
                    continue
                raise LLMUnavailableError(
                    "The AI provider could not be reached. Check the network connection and retry."
                ) from exc

            code, detail = self._error_of(response.status_code, payload)
            if code is None:
                try:
                    return self._to_response(request, payload)
                except LLMError as exc:
                    # A free model costs nothing, so the limit protects nothing:
                    # ask once more and let the model use its own maximum.
                    if exc.code == "llm_output_truncated" and limit and self._is_free_model():
                        logger.warning("openrouter answer was cut off; retrying without the output limit")
                        limit_lifted = True
                        continue
                    raise

            detail_lower = detail.lower()
            logger.warning("openrouter returned an error", extra={"status_code": code, "detail": self._redact(detail)})
            daily_limit = code == 429 and any(hint in detail_lower for hint in _DAILY_LIMIT_HINTS)
            if code in _TRANSIENT and not daily_limit and retries_left > 0:
                retries_left -= 1
                self._wait(attempt, response, payload)
                continue
            if self._degrade(code, detail_lower):
                continue
            raise self._to_error(code, detail_lower)

    def _send(self, request: LLMRequest, body: dict, attempt: int) -> tuple[httpx.Response, dict]:
        """POST the request and read the answer as it arrives, under one total time limit.

        Returns the response and a payload shaped like a non-streamed chat
        completion, whether OpenRouter answered with an event stream or, as
        it does for most errors, with a JSON body.
        """
        started = self._monotonic()
        reported = started
        state = {"model": "", "reasoning_chars": 0, "answer_chars": 0, "last_text_at": started}

        def tick() -> None:
            nonlocal reported
            now = self._monotonic()
            if now - started > self._timeout:
                raise _TimeLimitReached(now - started, state)
            if state["answer_chars"] and now - state["last_text_at"] > _STALL_SECONDS:
                raise _TimeLimitReached(now - started, state, stalled=True)
            if request.observer is not None and now - reported >= _REPORT_EVERY_SECONDS:
                reported = now
                phase = "writing" if state["answer_chars"] else "reasoning" if state["reasoning_chars"] else "waiting"
                request.observer({"model": state["model"] or self.model, "phase": phase, "elapsed": now - started, "attempt": attempt})

        with self._client.stream("POST", API_URL, headers=self._headers(), json=body) as response:
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raw = bytearray()
                for piece in response.iter_bytes():
                    raw += piece
                    tick()
                return response, self._json(bytes(raw))

            parts: list[str] = []
            payload: dict = {}
            choice: dict = {}
            for line in response.iter_lines():
                tick()  # also runs on keep-alive comment lines
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    payload["error"] = event["error"]
                if event.get("model"):
                    payload["model"] = state["model"] = event["model"]
                if event.get("usage"):
                    payload["usage"] = event["usage"]
                for item in event.get("choices") or []:
                    if not isinstance(item, dict):
                        continue
                    delta = item.get("delta") or item.get("message") or {}
                    if isinstance(delta.get("content"), str) and delta["content"]:
                        parts.append(delta["content"])
                        state["answer_chars"] += len(delta["content"])
                        state["last_text_at"] = self._monotonic()
                    if isinstance(delta.get("reasoning"), str):
                        state["reasoning_chars"] += len(delta["reasoning"])
                    if delta.get("refusal"):
                        choice["refusal"] = delta["refusal"]
                    if item.get("finish_reason"):
                        choice["finish_reason"] = item["finish_reason"]
                    if item.get("error"):
                        choice["error"] = item["error"]
            if parts or choice:
                payload["choices"] = [
                    {
                        "finish_reason": choice.get("finish_reason", ""),
                        "message": {"content": "".join(parts), "refusal": choice.get("refusal")},
                        **({"error": choice["error"]} if "error" in choice else {}),
                    }
                ]
            return response, payload

    def _time_limit_error(self, reached: _TimeLimitReached) -> LLMTimeoutError:
        model, seconds = reached.state["model"] or self.model, int(self._timeout)
        if reached.stalled:
            return LLMTimeoutError(
                f"The model {model} stopped part-way through its answer (nothing more arrived for {int(_STALL_SECONDS)} s). "
                "Retry, or set OPENROUTER_MODEL to another model."
            )
        if reached.state["answer_chars"]:
            return LLMTimeoutError(
                f"The model {model} was still writing its answer when the time limit ({seconds} s) was reached. "
                "Lower OPENROUTER_CHUNK_MAX_CHARS so each request is smaller, raise OPENROUTER_TIMEOUT_SECONDS, or set "
                "OPENROUTER_MODEL to a faster model."
            )
        if reached.state["reasoning_chars"]:
            return LLMTimeoutError(
                f"The model {model} spent the whole time limit ({seconds} s) reasoning without giving an answer. "
                "Keep OPENROUTER_REASONING=off, or set OPENROUTER_MODEL to another model."
            )
        return LLMTimeoutError(
            f"The model {model} did not answer within the time limit ({seconds} s). Retry, set OPENROUTER_MODEL to "
            "another model, or raise OPENROUTER_TIMEOUT_SECONDS."
        )

    def _is_free_model(self) -> bool:
        return self.model == "openrouter/free" or self.model.endswith(":free")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "PolicyGen AI",  # optional attribution shown in the OpenRouter dashboard
        }

    def _body(self, request: LLMRequest, max_tokens: int | None) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": True,
            "usage": {"include": True},  # token counts in the last stream event
        }
        if self._reasoning is not None:
            body["reasoning"] = self._reasoning
        if self._format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": request.task, "strict": True, "schema": inline_refs(request.schema)},
            }
        elif self._format == "json_object":
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        return body

    def _wait(self, attempt: int, response: httpx.Response | None = None, payload: dict | None = None) -> None:
        """Sleep before a retry: as long as the server asks for, otherwise exponential backoff."""
        seconds = self._server_wait(response, payload or {}) if response is not None else None
        if seconds is None:
            seconds = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
        self._sleep(min(max(seconds, 0.5), _MAX_WAIT_SECONDS))

    def _server_wait(self, response: httpx.Response, payload: dict) -> float | None:
        """Seconds from ``Retry-After``, or until ``X-RateLimit-Reset`` (epoch milliseconds)."""
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        sent = {str(k).lower(): v for k, v in (metadata.get("headers") or {}).items()} if isinstance(metadata.get("headers"), dict) else {}
        try:
            retry_after = response.headers.get("retry-after") or sent.get("retry-after")
            if retry_after:
                return float(retry_after)
            reset = response.headers.get("x-ratelimit-reset") or sent.get("x-ratelimit-reset")
            if reset:
                return float(reset) / 1000.0 - self._clock()
        except (TypeError, ValueError):
            pass
        return None

    # ---------------------------------------------------------------- errors
    @staticmethod
    def _json(raw: bytes) -> dict:
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_of(status: int, payload: dict) -> tuple[int | None, str]:
        """Return ``(code, message)`` for a failed call, or ``(None, "")`` for a usable one.

        OpenRouter reports some upstream failures with HTTP 200 and an
        ``error`` object in the body or on the choice, so both are checked.
        """
        error = payload.get("error")
        if not error:
            choices = payload.get("choices") or []
            error = choices[0].get("error") if choices and isinstance(choices[0], dict) else None
        if not error:
            if status < 400:
                return None, ""
            return status, f"HTTP {status}"
        if not isinstance(error, dict):
            return (status if status >= 400 else 502), str(error)
        code = error.get("code")
        code = code if isinstance(code, int) else (status if status >= 400 else 502)
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        return code, f"{error.get('message', '')} {metadata.get('raw', '')}".strip()

    def _redact(self, text: str) -> str:
        text = text[:300]
        return text.replace(self._api_key, "[redacted]") if self._api_key else text

    def _degrade(self, code: int, detail: str) -> bool:
        """Drop a request feature the chosen model cannot handle.  True when a retry is worthwhile."""
        if code not in (400, 404, 422):
            return False
        if self._reasoning is not None and "reasoning" in detail:
            logger.warning("openrouter model rejected the reasoning setting; continuing with the model default")
            self._reasoning = None
            return True
        if self._format != "none" and any(hint in detail for hint in _FORMAT_HINTS):
            self._format = _FORMAT_STEPS[_FORMAT_STEPS.index(self._format) + 1]
            logger.warning("openrouter model rejected the response format; stepping down", extra={"response_format": self._format})
            return True
        if self._max_output_tokens and any(hint in detail for hint in _TOKEN_HINTS):
            logger.warning("openrouter model rejected the output-token limit; continuing with the model default")
            self._max_output_tokens = None
            return True
        return False

    def _to_error(self, code: int, detail: str) -> LLMError:
        if code == 401:
            return LLMConfigurationError(
                "The OpenRouter API key was rejected. Check OPENROUTER_API_KEY in backend/.env: it must be a key from "
                "openrouter.ai/keys (they start with 'sk-or-'), not a key from another provider."
            )
        if code == 402:
            return LLMRateLimitError(
                "The OpenRouter account has no credit left for this model. Add credit at openrouter.ai, or set "
                "OPENROUTER_MODEL to a free model (an id ending in ':free', or 'openrouter/free').",
                code="llm_quota_exceeded",
            )
        if code == 403:
            if "moderation" in detail or "flagged" in detail:
                return LLMRefusalError("The AI provider declined to process this content.")
            return LLMConfigurationError(
                "OpenRouter refused this API key for the request (the key may be disabled or over its limit). "
                "Check OPENROUTER_API_KEY and the key's settings at openrouter.ai."
            )
        if code == 404 or "not a valid model" in detail or "model not found" in detail:
            return LLMConfigurationError(
                f"The model '{self.model}' is not available to this OpenRouter key. Check OPENROUTER_MODEL; free models "
                "must also be allowed in the privacy settings of the OpenRouter account."
            )
        if code == 429:
            if any(hint in detail for hint in _DAILY_LIMIT_HINTS):
                return LLMRateLimitError(
                    "The OpenRouter daily limit for free models was reached, so retrying today will not help. "
                    "Free models allow about 50 requests a day, or 1000 a day once the OpenRouter account has bought "
                    "at least 10 credits. Retry after the limit resets, or set OPENROUTER_MODEL to a paid model.",
                    code="llm_quota_exceeded",
                )
            return LLMRateLimitError(
                "The OpenRouter rate limit was reached and retries were exhausted (free models are limited per "
                "minute, and a busy free model is limited by its host). Wait a minute and retry, or choose another model."
            )
        if code in (408, 504, 524):
            return LLMTimeoutError(
                "The AI provider did not respond within the configured time limit. Retry, or raise LLM_TIMEOUT_SECONDS."
            )
        if code >= 500:
            return LLMUnavailableError("The AI provider is temporarily unavailable. Retry in a few minutes, or choose another model.")
        return LLMError(
            "The AI provider rejected the request. The document may be too large for this model; "
            "lower CHUNK_MAX_CHARS or choose a model with a larger context window.",
            code="llm_bad_request",
        )

    # -------------------------------------------------------------- response
    def _to_response(self, request: LLMRequest, payload: dict) -> LLMResponse:
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise LLMError("The AI provider returned an empty response. Retry the operation.", code="llm_empty_response")
        choice = choices[0]
        message = choice.get("message") or {}
        finish = choice.get("finish_reason") or ""
        if finish == "content_filter" or message.get("refusal"):
            raise LLMRefusalError("The AI provider declined to process this content.")
        if finish == "length":
            raise LLMError(
                "The AI response was cut off at the output limit. Raise OPENROUTER_MAX_OUTPUT_TOKENS, or set "
                "OPENROUTER_MODEL to a model with a larger output limit.",
                code="llm_output_truncated",
            )

        content = message.get("content")
        if isinstance(content, list):  # some models answer in content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        content = _THINKING.sub("", content, count=1) if isinstance(content, str) else ""
        usage = payload.get("usage") or {}
        served_by = payload.get("model") or self.model  # the model a router such as openrouter/free picked
        logger.info(
            "openrouter call completed",
            extra={
                "task": request.task,
                "model": served_by,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )
        return LLMResponse(
            text=content,
            model=served_by,
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
        )
