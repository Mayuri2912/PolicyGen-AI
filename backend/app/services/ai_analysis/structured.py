"""Validated structured calls to an LLM provider.

``structured_call`` is the only way the pipeline obtains model output.  It
parses the response as JSON, validates it against a Pydantic model and, when
that fails, asks the model to repair its answer a bounded number of times.
Malformed or incomplete output is never passed downstream.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from app import prompts
from app.core.logging import get_logger
from app.services.ai_analysis.providers.base import LLMInvalidResponseError, LLMProvider, LLMRequest, LLMResponse

logger = get_logger("llm.structured")

T = TypeVar("T", bound=BaseModel)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_MAX_ERROR_LINES = 25
_MAX_PREVIOUS_CHARS = 60_000


def parse_json_object(text: str) -> dict:
    """Extract the JSON object from a model response.

    Accepts bare JSON, JSON in a Markdown fence, or JSON surrounded by stray
    prose.  Raises ``ValueError`` when no object can be decoded.
    """
    candidate = _FENCE.sub("", (text or "").strip()).strip()
    if not candidate:
        raise ValueError("The response was empty.")
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("The response did not contain a JSON object.") from None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"The response was not valid JSON: {exc.msg} at position {exc.pos}.") from None
    if not isinstance(data, dict):
        raise ValueError("The response was JSON but not an object.")
    return data


def _describe(error: Exception) -> str:
    if isinstance(error, ValidationError):
        lines = [f"- {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in error.errors()]
        if len(lines) > _MAX_ERROR_LINES:
            lines = lines[:_MAX_ERROR_LINES] + [f"- ... and {len(lines) - _MAX_ERROR_LINES} more"]
        return "\n".join(lines)
    return f"- {error}"


def _duration(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"


def progress_reporter(update: Callable[..., None], activity: str) -> Callable[[dict], None]:
    """An ``LLMRequest.observer`` that keeps a job's message current during a model call.

    ``update`` is ``JobContext.update``: it stores the message the UI polls,
    and raises when the user cancelled the job, which ends the call.
    """

    def report(info: dict) -> None:
        doing = {"reasoning": "is reasoning", "writing": "is writing its answer"}.get(info.get("phase"), "has not answered yet")
        attempt = f", attempt {info['attempt']}" if info.get("attempt", 1) > 1 else ""
        update(message=f"{activity} {info.get('model') or 'The model'} {doing} ({_duration(info.get('elapsed', 0))}{attempt}).")

    return report


def structured_call(
    provider: LLMProvider,
    request: LLMRequest,
    model: type[T],
    *,
    repair_attempts: int = 1,
) -> tuple[T, LLMResponse]:
    """Call the provider and return a validated instance of ``model``.

    Raises ``LLMInvalidResponseError`` after ``repair_attempts`` failed repairs,
    and lets provider errors (timeouts, rate limits...) propagate unchanged.
    """
    current = request
    last_error = ""
    for attempt in range(repair_attempts + 1):
        response = provider.complete(current)
        try:
            return model.model_validate(parse_json_object(response.text)), response
        except (ValueError, ValidationError) as exc:
            last_error = _describe(exc)
            logger.warning(
                "llm output failed validation",
                extra={"task": request.task, "attempt": attempt + 1, "error_count": last_error.count("\n") + 1},
            )
            if attempt >= repair_attempts:
                break
            current = replace(
                request,
                user=prompts.render(
                    "json_repair_user",
                    errors=last_error,
                    previous=response.text[:_MAX_PREVIOUS_CHARS],
                    original=request.user,
                ),
            )
    raise LLMInvalidResponseError(
        "The AI returned a response that did not match the required structure, and automatic repair did not fix it. "
        "Retry the operation."
    )
