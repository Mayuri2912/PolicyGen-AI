"""OpenRouter provider: request shape, model compatibility, errors, selection, secrecy.

No test here calls OpenRouter.  The provider's real ``httpx`` client is given a
mock transport, so the URL, headers and JSON body are the ones production
sends; the opt-in live test is in test_live_openrouter.py.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from sqlalchemy import select

from app.core import config
from app.core.config import Settings
from app.database.session import session_scope
from app.models.entities import Analysis, Job
from app.schemas.analysis import ChunkFindings, ConsolidationResult, llm_json_schema
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.ai_analysis.providers.factory import PROVIDER_LABELS, get_provider, resolve_provider_name, set_provider_override
from app.services.ai_analysis.providers.mock_provider import MockProvider
from app.services.ai_analysis.providers.openrouter_provider import API_URL, OpenRouterProvider, inline_refs
from tests.conftest import all_text, analyze, generate, sample, upload_ok

FAKE_KEY = "TEST-OPENROUTER-KEY-not-real-0123456789"  # deliberately not in the real "sk-or-..." format


def completion(text: str = '{"ok": true}', *, finish: str = "stop", model: str = "vendor/model-x:free", **message):
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "model": model,
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text, **message}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        },
    )


def api_error(status: int, message: str, *, code: int | None = None, headers: dict | None = None, raw: str = ""):
    error = {"code": code or status, "message": message}
    if raw:
        error["metadata"] = {"raw": raw, "provider_name": "SomeUpstream"}
    return httpx.Response(status, json={"error": error}, headers=headers or {})


class Recorder:
    """Mock transport: replays outcomes and records the requests that were sent."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def openrouter(settings, monkeypatch):
    """Build the provider with a mock transport and no real sleeping."""
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/model-x:free")

    def build(outcomes) -> tuple[OpenRouterProvider, Recorder]:
        recorder = Recorder(outcomes)
        client = httpx.Client(transport=httpx.MockTransport(recorder))
        return OpenRouterProvider(settings, client=client, sleep=recorder.sleeps.append), recorder

    return build


def request(schema=None) -> LLMRequest:
    return LLMRequest("extract_findings", "system prompt", "user prompt", schema or {"type": "object"})


# ------------------------------------------------------------ request shape
def test_request_goes_to_the_chat_completions_endpoint_with_bearer_auth_and_schema(openrouter, settings):
    schema = llm_json_schema(ChunkFindings)
    provider, recorder = openrouter([completion('{"requirements": []}', reasoning="internal reasoning")])
    response = provider.complete(request(schema))

    assert response.text == '{"requirements": []}' and response.simulated is False
    assert response.model == "vendor/model-x:free" and (response.input_tokens, response.output_tokens) == (11, 22)
    (sent,) = recorder.requests
    assert sent.method == "POST" and str(sent.url) == "https://openrouter.ai/api/v1/chat/completions" == API_URL
    assert sent.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert sent.headers["content-type"] == "application/json"
    body = recorder.bodies()[0]
    assert body["model"] == "vendor/model-x:free"
    assert body["messages"] == [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "user prompt"}]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "extract_findings", "strict": True, "schema": inline_refs(schema)}}
    assert body["max_tokens"] == settings.openrouter_max_output_tokens
    assert provider.describe() == {
        "provider": "openrouter", "display_name": "OpenRouter", "model": "vendor/model-x:free",
        "simulated": False, "service_name": "OpenRouter API",
    }


def test_model_and_output_limit_come_from_configuration(openrouter, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_max_output_tokens", 0)
    monkeypatch.setattr(settings, "openrouter_structured_outputs", False)
    provider, recorder = openrouter([completion()])
    provider.complete(request())
    body = recorder.bodies()[0]
    assert "max_tokens" not in body  # 0 leaves the limit to the model
    assert body["response_format"] == {"type": "json_object"}

    monkeypatch.setattr(settings, "openrouter_model", "openrouter/free")
    other, recorder = openrouter([completion(model="some/free-model:free")])
    assert other.model == "openrouter/free"
    assert other.complete(request()).model == "some/free-model:free"  # the model the router actually used
    assert recorder.bodies()[0]["model"] == "openrouter/free"


def test_content_parts_are_joined(openrouter):
    parts = [{"type": "text", "text": '{"a": '}, {"type": "text", "text": "1}"}]
    provider, _ = openrouter([httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": parts}}]})])
    assert provider.complete(request()).text == '{"a": 1}'


# ------------------------------------------------- model compatibility
def test_models_without_structured_outputs_step_down_to_json_object_then_plain(openrouter):
    provider, recorder = openrouter(
        [
            api_error(404, "No endpoints found that support the requested parameters: response_format"),
            api_error(400, "Provider returned error", raw="This model does not support json_object response_format"),
            completion('{"ok": 1}'),
            completion('{"ok": 2}'),
        ]
    )
    assert provider.complete(request()).text == '{"ok": 1}'
    formats = [body.get("response_format", {}).get("type") for body in recorder.bodies()]
    assert formats == ["json_schema", "json_object", None]
    assert recorder.sleeps == []  # a capability mismatch is not a transient error: no waiting
    provider.complete(request())
    assert "response_format" not in recorder.bodies()[3]  # remembered for the following calls
    assert len(recorder.requests) == 4


def test_output_limit_above_the_models_maximum_is_dropped(openrouter):
    message = "This endpoint's maximum context length is 8192 tokens. However, you requested about 32400 tokens (400 of text input, 32000 in the output)."
    provider, recorder = openrouter([api_error(400, message), completion()])
    provider.complete(request())
    first, second = recorder.bodies()
    assert first["max_tokens"] == 32000 and "max_tokens" not in second


def test_downgrades_are_bounded(openrouter):
    rejection = api_error(400, "response_format is not supported")
    provider, recorder = openrouter([rejection, rejection, rejection, rejection])
    with pytest.raises(LLMError) as caught:
        provider.complete(request())
    assert caught.value.code == "llm_bad_request" and len(recorder.requests) == 3  # schema, json_object, plain - then stop


# ----------------------------------------------------------- error handling
@pytest.mark.parametrize(
    "outcome, expected, fragment",
    [
        (api_error(401, "No auth credentials found"), LLMConfigurationError, "OPENROUTER_API_KEY"),
        (api_error(401, "User not found."), LLMConfigurationError, "OPENROUTER_API_KEY"),
        (api_error(401, "Missing Authentication header"), LLMConfigurationError, "sk-or-"),  # what a key from another provider gets
        (api_error(429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"), LLMRateLimitError, "daily limit for free models"),
        (api_error(402, "Insufficient credits. Add more using https://openrouter.ai/credits"), LLMRateLimitError, "free model"),
        (api_error(403, "Key limit exceeded"), LLMConfigurationError, "OPENROUTER_API_KEY"),
        (api_error(403, "Your input was flagged by moderation"), LLMRefusalError, "declined"),
        (api_error(404, "No endpoints found for vendor/model-x:free."), LLMConfigurationError, "OPENROUTER_MODEL"),
        (api_error(404, "No endpoints found matching your data policy (Free model publication)."), LLMConfigurationError, "privacy settings"),
        (api_error(400, "vendor/model-x:free is not a valid model ID"), LLMConfigurationError, "OPENROUTER_MODEL"),
        (api_error(400, "Input validation error"), LLMError, "CHUNK_MAX_CHARS"),
    ],
)
def test_permanent_errors_are_mapped_and_not_retried(openrouter, outcome, expected, fragment):
    provider, recorder = openrouter([outcome])
    with pytest.raises(expected) as caught:
        provider.complete(request())
    message = caught.value.message
    assert fragment in message
    assert FAKE_KEY not in message and "Traceback" not in message and len(message) < 340
    assert len(recorder.requests) == 1 and recorder.sleeps == []


@pytest.mark.parametrize(
    "outcome, expected, fragment",
    [
        (api_error(429, "Rate limit exceeded: free-models-per-min. "), LLMRateLimitError, "Wait a minute"),
        (api_error(429, "Provider returned error", raw="vendor/model-x:free is temporarily rate-limited upstream. Please retry shortly."), LLMRateLimitError, "Wait a minute"),
        (api_error(502, "Provider returned error", raw="upstream connect error"), LLMUnavailableError, "temporarily unavailable"),
        (api_error(503, "No instances available"), LLMUnavailableError, "temporarily unavailable"),
        (api_error(408, "Request timed out"), LLMTimeoutError, "time limit"),
        (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), LLMUnavailableError, "could not be reached"),
        (httpx.ReadTimeout("The read operation timed out"), LLMTimeoutError, "time limit"),
    ],
)
def test_transient_errors_are_retried_a_bounded_number_of_times(openrouter, settings, outcome, expected, fragment):
    attempts = settings.llm_max_retries + 1
    provider, recorder = openrouter([outcome] * attempts)
    with pytest.raises(expected) as caught:
        provider.complete(request())
    assert fragment in caught.value.message and "getaddrinfo" not in caught.value.message
    assert len(recorder.requests) == attempts and len(recorder.sleeps) == settings.llm_max_retries
    assert all(0 < seconds <= 20 for seconds in recorder.sleeps)


def test_rate_limit_then_success_honours_retry_after(openrouter):
    provider, recorder = openrouter([api_error(429, "Rate limit exceeded", headers={"Retry-After": "3"}), completion('{"ok": 1}')])
    assert provider.complete(request()).text == '{"ok": 1}'
    assert recorder.sleeps == [3.0] and len(recorder.requests) == 2


def test_upstream_errors_reported_with_http_200_are_still_errors(openrouter, settings):
    in_body = httpx.Response(200, json={"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min"}})
    on_choice = httpx.Response(200, json={"choices": [{"finish_reason": "error", "error": {"code": 502, "message": "Upstream error"}, "message": {"content": ""}}]})
    provider, _ = openrouter([in_body] * (settings.llm_max_retries + 1))
    with pytest.raises(LLMRateLimitError):
        provider.complete(request())
    provider, _ = openrouter([on_choice] * (settings.llm_max_retries + 1))
    with pytest.raises(LLMUnavailableError):
        provider.complete(request())


def test_filtered_truncated_and_empty_responses_are_not_treated_as_answers(openrouter):
    provider, _ = openrouter(
        [
            completion("", finish="content_filter"),
            completion("", refusal="I can't help with that."),
            completion('{"a":', finish="length"),
            completion('{"a":', finish="length"),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, text="<html>gateway</html>"),
        ]
    )
    for _ in range(2):
        with pytest.raises(LLMRefusalError):
            provider.complete(request())
    with pytest.raises(LLMError) as truncated:
        provider.complete(request())
    assert truncated.value.code == "llm_output_truncated" and "OPENROUTER_MAX_OUTPUT_TOKENS" in truncated.value.message
    for _ in range(2):
        with pytest.raises(LLMError) as empty:
            provider.complete(request())
        assert empty.value.code == "llm_empty_response"


# ---------------------------------------------------------------- selection
def test_provider_selection_keeps_existing_providers(settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "auto")
    assert resolve_provider_name(settings) == "mock"
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    assert resolve_provider_name(settings) == "openrouter"  # auto: OpenRouter when it is the only key
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-key")
    assert resolve_provider_name(settings) == "gemini"  # existing providers keep their priority
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    assert resolve_provider_name(settings) == "anthropic"

    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    set_provider_override(None)
    provider = get_provider()
    assert isinstance(getattr(provider, "provider", provider), OpenRouterProvider) and provider.model == "openrouter/free"  # the default model

    monkeypatch.setattr(settings, "llm_provider", "mock")
    assert isinstance(get_provider(), MockProvider)
    assert set(PROVIDER_LABELS) == {"anthropic", "gemini", "openrouter", "groq", "nvidia", "mock"}


def test_openrouter_without_key_or_model_fails_clearly(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    set_provider_override(None)
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        get_provider()
    health = client.get("/api/health").json()
    assert health["status"] == "degraded"
    assert health["llm"] == {
        "provider": "openrouter", "display_name": "OpenRouter", "model": "openrouter/free", "simulated": False,
        "service_name": "", "ready": False,
        "message": "No OpenRouter API key is configured. Set OPENROUTER_API_KEY in backend/.env, or set LLM_PROVIDER=mock to use the simulated mode.",
    }
    started = client.post("/api/analyses", json={"file_ids": upload_ok(client, sample("sample_security.txt"))}).json()
    job = client.get(f"/api/jobs/{started['job']['id']}").json()
    assert job["status"] == "failed" and "No OpenRouter API key is configured" in job["error_message"]

    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "  ")
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_MODEL"):
        get_provider()


def test_health_and_config_report_the_configured_provider_and_model(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/some-model:free")
    set_provider_override(None)
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["llm"] == {
        "provider": "openrouter", "display_name": "OpenRouter", "model": "vendor/some-model:free", "simulated": False,
        "service_name": "OpenRouter API", "ready": True, "automatic_retries": 2,
        "message": "Documents are sent to the OpenRouter API for analysis and policy generation.",
    }
    assert "sent to the OpenRouter API" in health["privacy"]["llm"]
    assert client.get("/api/config").json()["llm_providers"] == PROVIDER_LABELS

    monkeypatch.setattr(settings, "openrouter_model", "vendor/another-model")  # a changed model is picked up
    assert client.get("/api/health").json()["llm"]["model"] == "vendor/another-model"


# ------------------------------------------------------------------ secrecy
def test_api_key_never_appears_in_api_responses_or_logs(client, settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    set_provider_override(None)
    with caplog.at_level(logging.DEBUG):
        bodies = [client.get(path).text for path in ("/api/health", "/api/config", "/api/dashboard", "/api/history", "/api/openapi.json")]

        # An upstream error that echoes the key: the user gets a message, the log gets a redacted detail.
        recorder = Recorder([api_error(401, f"Invalid credentials for key {FAKE_KEY}")])
        failing = OpenRouterProvider(settings, client=httpx.Client(transport=httpx.MockTransport(recorder)), sleep=lambda _s: None)
        set_provider_override(failing)
        started = client.post("/api/analyses", json={"file_ids": upload_ok(client, sample("sample_security.txt"))}).json()
        job = client.get(f"/api/jobs/{started['job']['id']}")
        assert job.json()["status"] == "failed" and "The OpenRouter API key was rejected" in job.json()["error_message"]
        bodies += [job.text, client.get(f"/api/analyses/{started['analysis_id']}").text, client.get("/api/history").text]

    assert all(FAKE_KEY not in body for body in bodies)
    logged = [r for r in caplog.records if r.name == "policygen.llm.openrouter" and hasattr(r, "detail")]
    assert logged and "[redacted]" in logged[0].detail
    for record in caplog.records:
        rendered = record.getMessage() + " " + " ".join(str(v) for v in record.__dict__.values())
        assert FAKE_KEY not in rendered, f"the API key leaked into a log record from {record.name}"


# ------------------------------------------- pipeline through OpenRouter
def answering_like_a_model(model: str = "vendor/model-x:free"):
    """A transport that answers each task with real, schema-valid JSON.

    The answers come from the rule engine only so the test has valid content to
    return; they travel through the genuine OpenRouter request and response
    handling, validation and labelling.
    """
    rules, seen = MockProvider(), []

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(http_request.content))
        return completion(rules.complete(handler.pending.pop(0)).text, model=model)

    handler.pending = []
    return handler, seen


def test_analysis_and_policy_generation_through_openrouter(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/model-x:free")
    handler, seen = answering_like_a_model()

    class Provider(OpenRouterProvider):
        def complete(self, llm_request):
            handler.pending.append(llm_request)  # lets the transport answer the request it is about to receive
            return super().complete(llm_request)

    set_provider_override(Provider(settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None))

    analysis = analyze(client, upload_ok(client, sample("sample_security.txt"), sample("sample_risk_assessment.xlsx")))
    assert (analysis["provider"], analysis["model"], analysis["simulated"]) == ("openrouter", "vendor/model-x:free", False)
    # Document analysis, risk identification, control mapping and gap analysis all ran on the provider's output.
    assert analysis["requirements"] and analysis["risks"] and analysis["controls"] and analysis["gaps"]
    assert all(r["source"]["evidence_verified"] for r in analysis["requirements"])
    assert {"score", "level", "likelihood", "impact", "origin"} <= set(analysis["risks"][0])
    assert any(c["related_risks"] for c in analysis["controls"]) and analysis["control_matrix"]

    report = generate(client, analysis["id"], policy_type="information_security", organization_name="Brightwater Analytics Pvt. Ltd.")
    assert report["status"] == "completed" and report["quality_report"]["counts"]["error"] == 0
    assert (report["provider"], report["model"], report["simulated"]) == ("openrouter", "vendor/model-x:free", False)
    assert "PolicyGen AI using vendor/model-x:free (OpenRouter)" not in all_text(report)
    assert "SIMULATED OUTPUT" not in all_text(report)
    assert client.get(f"/api/reports/{report['id']}/download?format=docx").content[:2] == b"PK"

    # The same prompts and schemas as every other provider reached OpenRouter.
    tasks = [body["response_format"]["json_schema"]["name"] for body in seen]
    assert tasks[-6:] == ["consolidate"] + ["generate_policy"] * 5 and len(tasks) >= 8 and set(tasks[:-6]) == {"extract_findings"}
    first = seen[0]
    assert "analysis engine of PolicyGen AI" in first["messages"][0]["content"]
    assert "<untrusted_document id=" in first["messages"][1]["content"]
    assert first["response_format"]["json_schema"]["schema"] == inline_refs(llm_json_schema(ChunkFindings))


# ------------------------------------------------ fixes found while debugging
def test_schema_references_are_inlined_for_hosts_that_need_self_contained_schemas():
    schema = {
        "$defs": {"Item": {"type": "object", "properties": {"tag": {"$ref": "#/$defs/Tag"}}}, "Tag": {"type": "string"}},
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}, "first": {"$ref": "#/$defs/Item", "description": "kept"}},
    }
    item = {"type": "object", "properties": {"tag": {"type": "string"}}}
    assert inline_refs(schema) == {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item}, "first": {**item, "description": "kept"}},
    }
    for model in (ChunkFindings, ConsolidationResult):
        flat = json.dumps(inline_refs(llm_json_schema(model)))
        assert "$ref" not in flat and "$defs" not in flat

    plain = {"type": "object"}
    assert inline_refs(plain) is plain
    recursive = {"$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}}, "$ref": "#/$defs/Node"}
    assert inline_refs(recursive) is recursive  # cannot be expanded: sent as written
    dangling = {"$defs": {"A": {"type": "string"}}, "properties": {"x": {"$ref": "#/$defs/Missing"}}}
    assert inline_refs(dangling) is dangling


def test_daily_free_limit_is_permanent_and_stops_the_analysis(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    daily = api_error(429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day")
    recorder = Recorder([daily] * 10)
    provider = OpenRouterProvider(settings, client=httpx.Client(transport=httpx.MockTransport(recorder)), sleep=recorder.sleeps.append)
    set_provider_override(provider)

    # Two files mean two extraction requests and one consolidation request.
    file_ids = upload_ok(client, sample("sample_security.txt"), sample("sample_risk_assessment.xlsx"))
    started = client.post("/api/analyses", json={"file_ids": file_ids}).json()
    job = client.get(f"/api/jobs/{started['job']['id']}").json()
    analysis = client.get(f"/api/analyses/{started['analysis_id']}").json()

    assert len(recorder.requests) == 1 and recorder.sleeps == []  # no retries, no further segments
    assert job["status"] == "failed" and job["stage"] == "failed"
    assert "daily limit for free models" in job["error_message"] and "AI analysis stopped" in job["error_message"]
    # Never shown as completed, and the reason is on the analysis itself.
    assert analysis["status"] == "failed" and not analysis.get("requirements")
    assert "daily limit for free models" in analysis["error_message"]


def test_minute_rate_limit_waits_until_the_reset_time_the_server_gives(settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    now = 1_790_000_000.0
    limited = httpx.Response(429, json={"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min. ",
                                                  "metadata": {"headers": {"X-RateLimit-Reset": str(int((now + 12) * 1000))}}}})
    far_away = httpx.Response(429, json={"error": {"code": 429, "message": "Rate limit exceeded"}}, headers={"X-RateLimit-Reset": str(int((now + 900) * 1000))})
    recorder = Recorder([limited, far_away, completion('{"ok": 1}')])
    provider = OpenRouterProvider(settings, client=httpx.Client(transport=httpx.MockTransport(recorder)), sleep=recorder.sleeps.append, clock=lambda: now)
    assert provider.complete(request()).text == '{"ok": 1}'
    assert recorder.sleeps == [12.0, 60.0]  # the stated reset, and never longer than a minute


def test_truncated_answer_from_a_free_model_is_retried_once_without_the_limit(openrouter, settings, monkeypatch):
    provider, recorder = openrouter([completion('{"a":', finish="length"), completion('{"a": 1}')])
    assert provider.complete(request()).text == '{"a": 1}'
    first, second = recorder.bodies()
    assert first["max_tokens"] == settings.openrouter_max_output_tokens and "max_tokens" not in second
    # The configured limit still applies to the next call.
    recorder.outcomes.append(completion())
    provider.complete(request())
    assert recorder.bodies()[2]["max_tokens"] == settings.openrouter_max_output_tokens

    # A paid model keeps its limit: the limit is what bounds the cost.
    monkeypatch.setattr(settings, "openrouter_model", "vendor/paid-model")
    paid, recorder = openrouter([completion('{"a":', finish="length"), completion('{"a": 1}')])
    with pytest.raises(LLMError) as truncated:
        paid.complete(request())
    assert truncated.value.code == "llm_output_truncated" and len(recorder.requests) == 1


def test_reasoning_preamble_is_removed_from_the_answer(openrouter):
    provider, _ = openrouter([completion('<think>\nThe user wants {"x": 0}...\n</think>\n\n{"a": 1}'), completion(None)])
    assert provider.complete(request()).text == '{"a": 1}'
    assert provider.complete(request()).text == ""  # an empty answer goes to validation, which asks again


def test_explicit_openrouter_never_falls_back_to_another_configured_provider(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-key")  # another provider is fully configured
    monkeypatch.setattr(settings, "anthropic_api_key", "anthropic-test-key")
    set_provider_override(None)
    with pytest.raises(LLMConfigurationError, match="OPENROUTER_API_KEY"):
        get_provider()
    llm = client.get("/api/health").json()["llm"]
    assert (llm["provider"], llm["ready"], llm["simulated"]) == ("openrouter", False, False)


def test_keys_and_model_ids_are_trimmed():
    loaded = Settings(openrouter_api_key='  "some-key"\r', openrouter_model=" openrouter/free ", gemini_api_key="'k' ")
    assert (loaded.openrouter_api_key, loaded.openrouter_model, loaded.gemini_api_key) == ("some-key", "openrouter/free", "k")


def test_editing_the_env_file_changes_the_provider_without_a_restart(client, monkeypatch):
    """A server that kept the old provider after .env was edited looked like a fallback to Gemini."""
    for name in ("LLM_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "GEMINI_API_KEY"):
        monkeypatch.delenv(name)  # so the file, not the test environment, decides
    monkeypatch.setattr(config, "_llm_reloaded", None)
    set_provider_override(None)
    env_file = config.ENV_FILE
    assert not env_file.exists()
    try:
        assert client.get("/api/health").json()["llm"]["provider"] == "mock"  # what the server started with

        env_file.write_text("LLM_PROVIDER=openrouter\nGEMINI_API_KEY=gemini-test-key\nOPENROUTER_MODEL=vendor/model-x:free\n", encoding="utf-8")
        llm = client.get("/api/health").json()["llm"]
        assert (llm["provider"], llm["ready"]) == ("openrouter", False) and "OPENROUTER_API_KEY" in llm["message"]

        env_file.write_text(f"LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY={FAKE_KEY}\nOPENROUTER_MODEL=vendor/model-x:free\n", encoding="utf-8")
        llm = client.get("/api/health").json()["llm"]
        assert (llm["provider"], llm["model"], llm["ready"]) == ("openrouter", "vendor/model-x:free", True)
        assert isinstance(getattr(get_provider(), "provider", get_provider()), OpenRouterProvider)

        # An edit that does not validate keeps the last good settings instead of breaking the server.
        env_file.write_text("LLM_PROVIDER=openrouter\nOPENROUTER_MAX_OUTPUT_TOKENS=lots\n", encoding="utf-8")
        assert client.get("/api/health").json()["llm"]["model"] == "vendor/model-x:free"
    finally:
        env_file.unlink(missing_ok=True)
        monkeypatch.setattr(config, "_llm_reloaded", None)
    assert client.get("/api/health").json()["llm"]["provider"] == "mock"


# ---------------------------------------------------- the stuck analysis
# What OpenRouter really sends: an event stream that starts at once and is
# kept alive with comment lines while the model works.
def sse(*events, keepalives: int = 0, done: bool = True) -> httpx.Response:
    lines = [": OPENROUTER PROCESSING"] * keepalives
    lines += [f"data: {json.dumps(event)}" for event in events]
    if done:
        lines.append("data: [DONE]")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=("\n\n".join(lines) + "\n\n").encode())


def delta(model: str = "vendor/model-x:free", *, finish: str | None = None, **fields) -> dict:
    return {"model": model, "choices": [{"delta": fields, "finish_reason": finish}]}


class Clock:
    """A monotonic clock that advances by ``step`` seconds every time it is read."""

    def __init__(self, step: float):
        self.step, self.now = step, 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def streaming(settings, outcomes, *, step: float = 0.0):
    recorder = Recorder(outcomes)
    provider = OpenRouterProvider(
        settings, client=httpx.Client(transport=httpx.MockTransport(recorder)), sleep=recorder.sleeps.append, monotonic=Clock(step)
    )
    return provider, recorder


def test_streamed_answer_is_assembled_and_reasoning_is_switched_off(openrouter, settings):
    answer = sse(
        delta(reasoning="thinking about it"),
        delta(content='{"requirements"'),
        delta(content=": []}"),
        {"model": "vendor/model-x:free", "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 9}},
        keepalives=3,
    )
    provider, recorder = openrouter([answer])
    response = provider.complete(request())
    assert response.text == '{"requirements": []}' and response.model == "vendor/model-x:free"
    assert (response.input_tokens, response.output_tokens) == (7, 9)
    body = recorder.bodies()[0]
    assert body["stream"] is True
    assert body["reasoning"] == {"enabled": False}  # free reasoning models otherwise think for minutes first


@pytest.mark.parametrize("value, sent", [("off", {"enabled": False}), ("low", {"effort": "low"}), ("high", {"effort": "high"}), ("default", None)])
def test_reasoning_setting_comes_from_configuration(openrouter, settings, monkeypatch, value, sent):
    monkeypatch.setattr(settings, "openrouter_reasoning", value)
    provider, recorder = openrouter([completion()])
    provider.complete(request())
    assert recorder.bodies()[0].get("reasoning") == sent


def test_a_model_that_cannot_switch_reasoning_off_gets_its_default(openrouter):
    refusal = api_error(400, "Provider returned error", raw="Reasoning is mandatory for this endpoint and cannot be disabled.")
    provider, recorder = openrouter([refusal, completion('{"ok": 1}'), completion('{"ok": 2}')])
    assert provider.complete(request()).text == '{"ok": 1}'
    first, second = recorder.bodies()
    assert "reasoning" in first and "reasoning" not in second and recorder.sleeps == []
    provider.complete(request())
    assert "reasoning" not in recorder.bodies()[2]  # remembered


def test_analysis_request_deadline_does_not_change_generation_deadline(settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    provider, recorder = streaming(settings, [sse(keepalives=400, done=False)], step=2.0)
    r = request()
    r.timeout_seconds = 180
    with pytest.raises(LLMTimeoutError, match="time limit \\(180 s\\)"):
        provider.complete(r)
    assert len(recorder.requests) == 1
    assert provider._monotonic.now < 200
    assert provider._timeout == settings.openrouter_timeout_seconds == 600


def test_a_request_the_model_never_finishes_ends_at_the_total_time_limit(settings, monkeypatch):
    """The cause of the stuck analysis: keep-alive bytes kept the connection, and the job, waiting for ever."""
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    assert settings.openrouter_timeout_seconds == 600  # the default: longer than LLM_TIMEOUT_SECONDS, never unlimited
    monkeypatch.setattr(settings, "openrouter_timeout_seconds", 300)
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    # The model reasons and never answers; the stream never ends by itself.
    thinking = [delta("vendor/slow-thinker:free", reasoning="still thinking... ")] * 400
    provider, recorder = streaming(settings, [sse(*thinking, done=False), sse(*thinking, done=False), sse(*thinking, done=False)], step=2.0)
    seen = []
    with pytest.raises(LLMTimeoutError) as caught:
        provider.complete(LLMRequest("extract_findings", "system", "user", {"type": "object"}, observer=seen.append))

    assert "vendor/slow-thinker:free spent the whole time limit (300 s) reasoning" in caught.value.message
    assert "OPENROUTER_REASONING" in caught.value.message and "OPENROUTER_MODEL" in caught.value.message
    assert len(recorder.requests) == 2  # one more try, not LLM_MAX_RETRIES more waits of the full limit
    # The observer was told what the model was doing while it waited.
    assert seen and {info["phase"] for info in seen} == {"reasoning"} and seen[0]["model"] == "vendor/slow-thinker:free"
    assert [info["attempt"] for info in seen][0] == 1 and seen[-1]["attempt"] == 2
    assert all(0 < info["elapsed"] <= 300 for info in seen)

    # Still writing at the limit, and silent until the limit, give different advice.
    writing = [delta(content="x")] * 400
    provider, _ = streaming(settings, [sse(*writing, done=False)] * 2, step=2.0)
    with pytest.raises(LLMTimeoutError, match="still writing its answer.*OPENROUTER_CHUNK_MAX_CHARS"):
        provider.complete(request())
    provider, _ = streaming(settings, [sse(keepalives=400, done=False)] * 2, step=2.0)
    with pytest.raises(LLMTimeoutError, match="did not answer within the time limit"):
        provider.complete(request())
    # The same limit applies when the answer is not an event stream.
    provider, _ = streaming(settings, [httpx.Response(200, content=b" " * 100_000 + b"{}")] * 2, step=400.0)
    with pytest.raises(LLMTimeoutError, match="did not answer within the time limit"):
        provider.complete(request())


def test_errors_inside_the_stream_are_handled_like_any_other(openrouter, settings):
    daily = sse({"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})
    provider, recorder = openrouter([daily])
    with pytest.raises(LLMRateLimitError) as caught:
        provider.complete(request())
    assert caught.value.code == "llm_quota_exceeded" and len(recorder.requests) == 1

    upstream = sse(delta(content='{"a"'), {"choices": [{"delta": {}, "finish_reason": "error", "error": {"code": 502, "message": "Upstream error"}}]})
    provider, recorder = openrouter([upstream, sse(delta(content='{"a": 1}', finish="stop"))])
    assert provider.complete(request()).text == '{"a": 1}'  # transient: retried, and the partial answer is discarded
    assert len(recorder.requests) == 2

    provider, _ = openrouter([sse(delta(content='{"a":', finish="length")), sse(delta(content='{"a": 2}', finish="stop"))])
    assert provider.complete(request()).text == '{"a": 2}'  # cut off on a free model: asked again without the limit
    provider, _ = openrouter([sse()])
    with pytest.raises(LLMError) as empty:
        provider.complete(request())
    assert empty.value.code == "llm_empty_response"


def big_document(rules: int = 160) -> tuple[str, bytes]:
    lines = ["Northgate Dental Clinic - Information Handling Rules (fictional test document)", ""]
    lines += [f"{n}. Staff must record every access to patient file cabinet {n} in the access register on the same day." for n in range(1, rules + 1)]
    return "northgate_rules.txt", "\n".join(lines).encode()


def test_documents_go_to_openrouter_in_small_parts_with_live_progress(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/model-x:free")
    handler, seen = answering_like_a_model()
    messages = []

    class Provider(OpenRouterProvider):
        def complete(self, llm_request):
            handler.pending.append(llm_request)
            with session_scope() as db:  # what the UI would be polling at this moment
                messages.append(db.scalars(select(Job).where(Job.type == "analysis")).one().message)
            llm_request.observer({"model": "vendor/model-x:free", "phase": "writing", "elapsed": 75, "attempt": 1})
            with session_scope() as db:
                messages.append(db.scalars(select(Job).where(Job.type == "analysis")).one().message)
            return super().complete(llm_request)

    provider = Provider(settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None)
    assert provider.chunk_max_chars == settings.openrouter_chunk_max_chars == 5000 < settings.chunk_max_chars
    set_provider_override(provider)

    name, data = big_document()
    assert len(data) > 3 * settings.openrouter_chunk_max_chars
    analysis = analyze(client, upload_ok(client, (name, data)))

    extracts = [body for body in seen if body["response_format"]["json_schema"]["name"] == "extract_findings"]
    assert len(extracts) >= 4 and len(seen) == len(extracts) + 1  # several small requests, then one consolidation
    rules = [body["messages"][1]["content"].count("in the access register on the same day.") for body in extracts]
    assert sum(rules) == 160 and max(rules) <= 5000 // 90  # every rule sent exactly once, in parts of at most 5000 characters
    assert analysis["completed"] and len(analysis["requirements"]) >= 100  # nothing was dropped by splitting
    assert messages[0] == f"Analyzing {name} (part 1 of {len(extracts)})."
    assert messages[1] == f"Analyzing {name} (part 1 of {len(extracts)}). vendor/model-x:free is writing its answer (1 min 15 s)."
    assert messages[-1] == "Consolidating findings across documents. vendor/model-x:free is writing its answer (1 min 15 s)."


def test_cancel_stops_a_request_in_flight_and_retry_reuses_the_same_job(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_model", "vendor/model-x:free")
    answers, seen = answering_like_a_model()
    state = {"cancel": True, "requests": 0}

    def handler(http_request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        if state["cancel"]:
            # The user presses Cancel while the model is still working on this request.
            with session_scope() as db:
                job = db.scalars(select(Job).where(Job.type == "analysis")).one()
                assert client.post(f"/api/jobs/{job.id}/cancel").json()["cancel_requested"] is True
            return sse(*[delta(reasoning="thinking ")] * 50, done=False)
        return answers(http_request)

    class Provider(OpenRouterProvider):
        def complete(self, llm_request):
            answers.pending.append(llm_request)
            return super().complete(llm_request)

    set_provider_override(Provider(settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None, monotonic=Clock(2.0)))
    file_ids = upload_ok(client, sample("sample_security.txt"))
    started = client.post("/api/analyses", json={"file_ids": file_ids}).json()
    job = client.get(f"/api/jobs/{started['job']['id']}").json()
    assert (job["status"], job["stage"]) == ("cancelled", "cancelled") and state["requests"] == 1  # stopped mid-request, no retries
    assert client.get(f"/api/analyses/{started['analysis_id']}").json()["status"] == "cancelled"

    state["cancel"] = False
    answers.pending.clear()
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["id"] == job["id"] and retried["status"] == "completed" and retried["attempts"] == 2
    analysis = client.get(f"/api/analyses/{started['analysis_id']}").json()
    assert analysis["completed"] and analysis["requirements"]
    with session_scope() as db:  # one analysis and one analysis job: nothing was duplicated
        assert len(db.scalars(select(Analysis)).all()) == 1
        assert len(db.scalars(select(Job).where(Job.type == "analysis")).all()) == 1
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409  # a finished job cannot be started again
    # Asking for the same analysis again does not disturb the finished one.
    again = client.post("/api/analyses", json={"file_ids": file_ids}).json()
    assert again["analysis_id"] != started["analysis_id"]
    assert client.get(f"/api/analyses/{started['analysis_id']}").json()["completed"]


def test_a_timed_out_analysis_is_reported_as_failed_with_the_reason(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    monkeypatch.setattr(settings, "openrouter_timeout_seconds", 0)  # 0 falls back to LLM_TIMEOUT_SECONDS
    thinking = sse(*[delta("vendor/slow-thinker:free", reasoning="hmm ")] * 400, done=False)
    provider, recorder = streaming(settings, [thinking] * 4, step=2.0)
    set_provider_override(provider)
    started = client.post("/api/analyses", json={"file_ids": upload_ok(client, sample("sample_security.txt"))}).json()
    job = client.get(f"/api/jobs/{started['job']['id']}").json()
    assert job["status"] == "failed" and job["result"]["failed_stage"] == "analyzing"
    assert f"spent the whole time limit ({settings.analysis_call_timeout_seconds} s)" in job["error_message"] and "vendor/slow-thinker:free" in job["error_message"]
    analysis = client.get(f"/api/analyses/{started['analysis_id']}").json()
    assert analysis["status"] == "failed" and not analysis["completed"]


def test_a_model_that_stalls_part_way_is_given_up_on_before_the_total_limit(settings, monkeypatch):
    """Seen live: a free model wrote 1,791 characters and then nothing more for the rest of the 300 seconds."""
    monkeypatch.setattr(settings, "openrouter_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "openrouter_timeout_seconds", 300)
    stalled = sse(delta("vendor/staller:free", content='{"title": "Access'), keepalives=0, done=False)
    stalled = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stalled.content + b": OPENROUTER PROCESSING\n\n" * 200)
    good = sse(delta("vendor/other:free", content='{"ok": 1}', finish="stop"))

    provider, recorder = streaming(settings, [stalled, good], step=1.0)
    response = provider.complete(request())
    assert response.text == '{"ok": 1}' and response.model == "vendor/other:free"  # the second try reached another model
    assert len(recorder.requests) == 2 and provider._monotonic.now < 200  # given up after ~90 s, not 300

    provider, recorder = streaming(settings, [stalled, stalled, stalled], step=1.0)
    with pytest.raises(LLMTimeoutError, match="vendor/staller:free stopped part-way through its answer"):
        provider.complete(request())
    assert len(recorder.requests) == 2

    # A host that sends the whole answer at the end is not a stall, however long the keep-alives last.
    buffered = httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=b": OPENROUTER PROCESSING\n\n" * 120 + sse(delta(content='{"ok": 2}', finish="stop")).content,
    )
    provider, _ = streaming(settings, [buffered], step=1.0)
    assert provider.complete(request()).text == '{"ok": 2}'
