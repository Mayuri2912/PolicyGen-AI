import json

import httpx
import pytest
from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMRateLimitError,
    LLMRequest,
)
from app.services.ai_analysis.providers.factory import (
    get_provider,
    set_provider_override,
)
from app.services.ai_analysis.providers.groq_provider import GroqProvider


def test_groq_transport_and_payload(settings, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "test-secret")
    def respond(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == settings.groq_model
        assert body["response_format"] == {"type": "json_object"}
        assert "usage" not in body and "reasoning" not in body and "max_tokens" not in body
        assert 512 <= body["max_completion_tokens"] <= 7500
        event = {"model": settings.groq_model, "choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    provider = GroqProvider(settings, client=httpx.Client(transport=httpx.MockTransport(respond)))
    result = provider.complete(LLMRequest("test", "Return JSON", "Return JSON", {}))
    assert json.loads(result.text) == {"ok": True}
    assert provider.describe()["provider"] == "groq"
    assert "test-secret" not in provider._redact("test-secret")


def test_groq_failure_mapping(settings):
    provider = GroqProvider(settings)
    assert isinstance(provider._to_error(401, "private"), LLMConfigurationError)
    assert isinstance(provider._to_error(429, "private"), LLMRateLimitError)
    assert "private" not in provider._to_error(400, "private").message


def test_groq_factory_and_key_change(settings, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "first")
    set_provider_override(None)
    first = get_provider()
    assert first.name == "groq"
    monkeypatch.setattr(settings, "groq_api_key", "second")
    assert get_provider() is not first
    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(LLMConfigurationError):
        get_provider()


def test_groq_size_limit_is_clear_and_not_retried(settings):
    from app.services.ai_analysis.providers.base import LLMError
    provider = GroqProvider(settings)
    error = provider._to_error(413, "private account details")
    assert error.code == "llm_request_too_large" and not error.retryable
    assert "private" not in error.message
    with pytest.raises(LLMError, match="token budget"):
        provider._body(LLMRequest("consolidate", "system", "x" * 90000, {}), 32000)


def test_groq_rate_limit_wait_from_message(settings):
    provider = GroqProvider(settings)
    response = httpx.Response(429)
    assert provider._server_wait(response, {"error": {"message": "Please try again in 35.8875s."}}) == 35.8875


def test_groq_truncation_message_names_correct_provider(settings):
    from app.services.ai_analysis.providers.base import LLMError
    provider = GroqProvider(settings)
    body = provider._body(LLMRequest("consolidate", "system", "user", {}), 4500)
    assert body["reasoning_effort"] == "low"
    with pytest.raises(LLMError, match="Groq") as error:
        provider._to_response(LLMRequest("consolidate", "system", "user", {}), {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
    assert "OPENROUTER" not in error.value.message


def test_daily_quota_and_wait_duration_units(settings):
    provider = GroqProvider(settings)
    response = httpx.Response(429)
    assert provider._to_error(429, "tokens per day (TPD)").code == "llm_quota_exceeded"
    assert provider._server_wait(response, {"error": {"message": "Please try again in 31m34.32s."}}) == 1894.32
    assert provider._server_wait(response, {"error": {"message": "Please try again in 150ms."}}) == 0.15
