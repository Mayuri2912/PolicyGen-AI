"""Google Gemini provider: request shape, error handling, selection, secrecy.

No test here calls Google.  A stand-in client replays responses and raises the
SDK's own exception types; the opt-in live test is in test_live_gemini.py.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from app.schemas.analysis import ChunkFindings, llm_json_schema
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.services.ai_analysis.providers.factory import (
    get_provider,
    resolve_provider_name,
    set_provider_override,
)
from app.services.ai_analysis.providers.gemini_provider import GeminiProvider
from app.services.ai_analysis.providers.mock_provider import MockProvider
from google.genai import errors
from tests.conftest import all_text, analyze, generate, sample, upload_ok

FAKE_KEY = "TEST-GEMINI-KEY-not-real-0123456789"  # deliberately not in the real "AIza..." key format


def gemini_response(text: str = '{"ok": true}', finish: str = "STOP", *, block_reason=None, thought: str = ""):
    """An object shaped like the SDK's GenerateContentResponse."""
    parts = []
    if thought:
        parts.append(SimpleNamespace(text=thought, thought=True))
    parts.append(SimpleNamespace(text=text, thought=None))
    return SimpleNamespace(
        prompt_feedback=SimpleNamespace(block_reason=block_reason) if block_reason else None,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish), content=SimpleNamespace(parts=parts))],
        usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=22),
        model_version="gemini-3.8-flash",
    )


class FakeGeminiClient:
    """Stands in for ``genai.Client``: replays results or raises errors."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def api_error(cls, code: int, status: str, message: str, reason: str = ""):
    body = {"error": {"code": code, "status": status, "message": message}}
    if reason:
        body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
    return cls(code, body)


def provider_with(settings, outcomes) -> tuple[GeminiProvider, FakeGeminiClient]:
    fake = FakeGeminiClient(outcomes)
    return GeminiProvider(settings, client=fake), fake


def request(schema=None) -> LLMRequest:
    return LLMRequest("extract_findings", "system prompt", "user prompt", schema or {"type": "object"})


# ------------------------------------------------------------ request shape
def test_request_uses_system_instruction_json_mime_type_and_the_task_schema(settings):
    schema = llm_json_schema(ChunkFindings)
    provider, fake = provider_with(settings, [gemini_response('{"requirements": []}', thought="thinking about it")])
    response = provider.complete(request(schema))

    assert response.text == '{"requirements": []}'  # thought parts are not part of the answer
    assert response.simulated is False and response.model == "gemini-3.8-flash"
    assert (response.input_tokens, response.output_tokens) == (11, 22)
    (call,) = fake.calls
    assert call["model"] == "gemini-3.8-flash" and call["contents"] == "user prompt"
    config = call["config"]
    assert config.system_instruction == "system prompt"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema and config.response_schema is None
    assert config.max_output_tokens == settings.gemini_max_output_tokens
    assert provider.describe() == {
        "provider": "gemini", "display_name": "Google Gemini", "model": "gemini-3.8-flash",
        "simulated": False, "service_name": "Google Gemini API",
    }


def test_model_comes_from_configuration(settings, monkeypatch):
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.1-pro-preview")
    provider, fake = provider_with(settings, [gemini_response()])
    provider.complete(request())
    assert provider.model == "gemini-3.1-pro-preview" and fake.calls[0]["model"] == "gemini-3.1-pro-preview"


def test_real_client_is_built_with_timeout_and_bounded_retries(settings, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 120)
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    provider = GeminiProvider(settings)  # constructing the client makes no network call
    options = provider._client._api_client._http_options
    assert options.timeout == 120_000  # milliseconds
    assert options.retry_options.attempts == 3  # the first try plus LLM_MAX_RETRIES


# ----------------------------------------------------------- error handling
@pytest.mark.parametrize(
    "error, expected, fragment",
    [
        (api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"), LLMConfigurationError, "GEMINI_API_KEY"),
        (api_error(errors.ClientError, 403, "PERMISSION_DENIED", "Your API key was reported as leaked."), LLMConfigurationError, "GEMINI_API_KEY"),
        (api_error(errors.ClientError, 401, "UNAUTHENTICATED", "Request had invalid authentication credentials."), LLMConfigurationError, "GEMINI_API_KEY"),
        (api_error(errors.ClientError, 404, "NOT_FOUND", "models/gemini-9-ultra is not found for API version v1beta."), LLMConfigurationError, "GEMINI_MODEL"),
        (api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "models/embedding-001 is not supported for generateContent."), LLMConfigurationError, "GEMINI_MODEL"),
        (api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED", "You exceeded your current quota, please check your plan and billing details."), LLMRateLimitError, "quota"),
        (api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "The input token count exceeds the maximum number of tokens allowed."), LLMError, "CHUNK_MAX_CHARS"),
        (api_error(errors.ServerError, 503, "UNAVAILABLE", "The model is overloaded. Please try again later."), LLMUnavailableError, "temporarily unavailable"),
        (api_error(errors.ServerError, 500, "INTERNAL", "An internal error has occurred."), LLMUnavailableError, "temporarily unavailable"),
        (api_error(errors.ServerError, 504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete."), LLMTimeoutError, "time limit"),
        (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), LLMUnavailableError, "could not be reached"),
        (httpx.ReadTimeout("The read operation timed out"), LLMTimeoutError, "time limit"),
        (httpx.RemoteProtocolError("Server disconnected without sending a response."), LLMUnavailableError, "could not be reached"),
    ],
)
def test_errors_are_mapped_to_safe_actionable_messages(settings, monkeypatch, error, expected, fragment):
    monkeypatch.setattr(settings, "gemini_api_key", FAKE_KEY)
    provider, fake = provider_with(settings, [error])
    with pytest.raises(expected) as caught:
        provider.complete(request())
    message = caught.value.message
    assert fragment in message
    # The message shown to users never carries the key, the raw API text or internals.
    assert FAKE_KEY not in message and "Traceback" not in message and "getaddrinfo" not in message and len(message) < 260
    assert len(fake.calls) == 1  # a hard failure is not retried by the provider


def test_quota_error_is_retryable_and_invalid_key_is_not(settings):
    assert LLMRateLimitError("x").retryable is True and LLMConfigurationError("x").retryable is False


def test_rejected_schema_falls_back_to_prompt_enforced_json(settings):
    outcomes = [
        api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "The specified schema produces a constraint that has too many states for serving."),
        gemini_response('{"ok": 1}'),
        gemini_response('{"ok": 2}'),
    ]
    provider, fake = provider_with(settings, outcomes)
    assert provider.complete(request()).text == '{"ok": 1}'
    assert fake.calls[0]["config"].response_json_schema is not None
    assert fake.calls[1]["config"].response_json_schema is None
    assert fake.calls[1]["config"].response_mime_type == "application/json"  # still asks for JSON
    provider.complete(request())
    assert fake.calls[2]["config"].response_json_schema is None and len(fake.calls) == 3  # remembered, not retried each time


def test_rejected_output_limit_falls_back_to_the_model_default(settings):
    outcomes = [api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "max_output_tokens must be less than or equal to 8192."), gemini_response()]
    provider, fake = provider_with(settings, outcomes)
    provider.complete(request())
    assert fake.calls[0]["config"].max_output_tokens == settings.gemini_max_output_tokens
    assert fake.calls[1]["config"].max_output_tokens is None


def test_bad_requests_are_not_retried_forever(settings):
    outcomes = [api_error(errors.ClientError, 400, "INVALID_ARGUMENT", "Request contains an invalid argument.") for _ in range(5)]
    provider, fake = provider_with(settings, outcomes)
    with pytest.raises(LLMError) as caught:
        provider.complete(request())
    assert caught.value.code == "llm_bad_request" and len(fake.calls) == 1


def test_blocked_truncated_and_empty_responses_are_not_treated_as_answers(settings):
    provider, _ = provider_with(
        settings,
        [
            gemini_response("", block_reason="SAFETY"),
            gemini_response("partial", finish="SAFETY"),
            gemini_response('{"a":', finish="MAX_TOKENS"),
            SimpleNamespace(prompt_feedback=None, candidates=[], usage_metadata=None, model_version=None),
        ],
    )
    for _ in range(2):
        with pytest.raises(LLMRefusalError):
            provider.complete(request())
    with pytest.raises(LLMError) as truncated:
        provider.complete(request())
    assert truncated.value.code == "llm_output_truncated" and "GEMINI_MAX_OUTPUT_TOKENS" in truncated.value.message
    with pytest.raises(LLMError) as empty:
        provider.complete(request())
    assert empty.value.code == "llm_empty_response"


# ---------------------------------------------------------------- selection
def test_provider_selection(settings, monkeypatch):
    assert resolve_provider_name(settings) == "mock"  # no keys in the test environment

    monkeypatch.setattr(settings, "llm_provider", "auto")
    assert resolve_provider_name(settings) == "mock"
    monkeypatch.setattr(settings, "gemini_api_key", FAKE_KEY)
    assert resolve_provider_name(settings) == "gemini"  # auto: Gemini when it is the only key
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    assert resolve_provider_name(settings) == "anthropic"  # auto: Claude keeps priority, as before

    monkeypatch.setattr(settings, "llm_provider", "gemini")
    set_provider_override(None)
    provider = get_provider()
    assert isinstance(getattr(provider, "provider", provider), GeminiProvider) and provider.model == "gemini-3.8-flash"

    monkeypatch.setattr(settings, "llm_provider", "mock")  # mock mode still works with keys present
    assert isinstance(get_provider(), MockProvider)


def test_gemini_selected_without_a_key_fails_clearly_and_mock_still_works(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    set_provider_override(None)
    with pytest.raises(LLMConfigurationError, match="GEMINI_API_KEY"):
        get_provider()
    health = client.get("/api/health").json()
    assert health["status"] == "degraded" and health["llm"] == {
        "provider": "gemini", "display_name": "Google Gemini", "model": "gemini-3.8-flash", "simulated": False,
        "service_name": "", "ready": False,
        "message": "No Gemini API key is configured. Set GEMINI_API_KEY in backend/.env, or set LLM_PROVIDER=mock to use the simulated mode.",
    }
    started = client.post("/api/analyses", json={"file_ids": upload_ok(client, sample("sample_security.txt"))}).json()
    job = client.get(f"/api/jobs/{started['job']['id']}").json()
    assert job["status"] == "failed" and "No Gemini API key is configured" in job["error_message"]

    monkeypatch.setattr(settings, "llm_provider", "mock")
    assert client.get("/api/health").json()["llm"]["simulated"] is True
    retried = client.post(f"/api/jobs/{job['id']}/retry").json()
    assert retried["status"] == "completed"  # the same job succeeds in mock mode, no key needed


def test_unknown_provider_error_lists_gemini(settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "skynet")
    set_provider_override(None)
    with pytest.raises(LLMConfigurationError, match="auto, anthropic, gemini, groq, mock, nvidia, openrouter"):
        get_provider()


# ------------------------------------------------------------------ secrecy
def test_api_key_never_appears_in_api_responses_or_logs(client, settings, monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", FAKE_KEY)
    set_provider_override(None)
    with caplog.at_level(logging.DEBUG):
        health = client.get("/api/health")
        assert health.json()["llm"]["ready"] is True and health.json()["llm"]["service_name"] == "Google Gemini API"
        assert "Google Gemini API" in health.json()["privacy"]["llm"]
        bodies = [health.text] + [client.get(path).text for path in ("/api/config", "/api/dashboard", "/api/history", "/api/openapi.json")]

        # A rejected key: the failure reaches the user as a message, never as the key.
        failing = GeminiProvider(settings, client=FakeGeminiClient([api_error(errors.ClientError, 400, "INVALID_ARGUMENT", f"API key not valid: {FAKE_KEY}", "API_KEY_INVALID")]))
        set_provider_override(failing)
        started = client.post("/api/analyses", json={"file_ids": upload_ok(client, sample("sample_security.txt"))}).json()
        job = client.get(f"/api/jobs/{started['job']['id']}")
        analysis = client.get(f"/api/analyses/{started['analysis_id']}")
        assert job.json()["status"] == "failed" and "The Gemini API key was rejected" in job.json()["error_message"]
        bodies += [job.text, analysis.text, client.get("/api/history").text]

    assert all(FAKE_KEY not in body for body in bodies)
    for record in caplog.records:
        rendered = record.getMessage() + " " + " ".join(str(v) for v in record.__dict__.values())
        assert FAKE_KEY not in rendered, f"the API key leaked into a log record from {record.name}"


# ------------------------------------------------- pipeline with Gemini
class GeminiWithCannedAnswers(GeminiProvider):
    """The real provider with the network call replaced.

    ``complete`` (response handling, error mapping) is the production code;
    only ``_send`` is swapped for an answer in Gemini's response shape.
    """

    def __init__(self, settings):
        super().__init__(settings, client=FakeGeminiClient([]))
        self.sent: list[LLMRequest] = []
        self._answers = MockProvider()

    def _send(self, request: LLMRequest):
        self.sent.append(request)
        canned: LLMResponse = self._answers.complete(request)
        return gemini_response("```json\n" + canned.text + "\n```")  # fenced, as models sometimes answer


def test_analysis_and_policy_generation_through_the_gemini_provider(client, settings):
    provider = GeminiWithCannedAnswers(settings)
    set_provider_override(provider)

    analysis = analyze(client, upload_ok(client, sample("sample_security.txt"), sample("sample_risk_assessment.xlsx")))
    assert (analysis["provider"], analysis["model"], analysis["simulated"]) == ("gemini", "gemini-3.8-flash", False)
    # The same structured output the application expects from any provider.
    assert analysis["requirements"] and analysis["risks"] and analysis["controls"] and analysis["gaps"]
    assert all(r["ref_id"].startswith("REQ-") and r["source"]["evidence_verified"] for r in analysis["requirements"])
    assert {"score", "level", "likelihood", "impact", "origin"} <= set(analysis["risks"][0])
    assert len(analysis["gaps"]) == len(analysis["requirements"])

    report = generate(client, analysis["id"], policy_type="information_security", organization_name="Brightwater Analytics Pvt. Ltd.")
    assert report["status"] == "completed" and report["quality_report"]["counts"]["error"] == 0
    assert (report["provider"], report["model"], report["simulated"]) == ("gemini", "gemini-3.8-flash", False)
    assert "PolicyGen AI using gemini-3.8-flash (Google Gemini)" not in all_text(report)
    assert "SIMULATED OUTPUT" not in all_text(report)  # a real provider's draft is not labelled as mock
    assert client.get(f"/api/reports/{report['id']}/download?format=pdf").content.startswith(b"%PDF-")

    # Same prompts and schemas as every other provider; nothing Gemini-specific upstream.
    assert [r.task for r in provider.sent[:3]] == ["extract_findings", "extract_findings", "consolidate"]
    assert all(r.task == "generate_policy" for r in provider.sent[3:])
    assert len(provider.sent[3:]) == 5
    extract = provider.sent[0]
    assert "analysis engine of PolicyGen AI" in extract.system and "<untrusted_document id=" in extract.user
    assert extract.schema == llm_json_schema(ChunkFindings)
    json.dumps(extract.schema)  # the schema is plain JSON, as response_json_schema requires


def test_daily_and_zero_quota_do_not_retry_as_minute_limits(settings):
    from unittest.mock import Mock

    from google.genai import errors
    provider = GeminiProvider(settings, client=Mock())
    daily = errors.ClientError(429, {"error": {"message": "Quota GenerateRequestsPerDayPerProject exceeded", "status": "RESOURCE_EXHAUSTED", "details": [{"retryDelay": "30s"}]}})
    error = provider._client_error(daily)
    assert error.code == "llm_quota_exceeded" and error.retry_after_seconds == 86400
    zero = errors.ClientError(429, {"error": {"message": "Quota exceeded, limit: 0, model: unavailable", "status": "RESOURCE_EXHAUSTED"}})
    assert isinstance(provider._client_error(zero), LLMConfigurationError)
