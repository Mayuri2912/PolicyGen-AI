import json

import httpx
import pytest

from app.services.ai_analysis.providers.base import (
    LLMConfigurationError,
    LLMError,
    LLMRefusalError,
    LLMRequest,
)
from app.services.ai_analysis.providers.factory import (
    get_provider,
    set_provider_override,
)
from app.services.ai_analysis.providers.nvidia_provider import NvidiaProvider


def test_nvidia_stream_payload_and_redaction(settings):
    def respond(request):
        assert str(request.url) == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer fake-nvidia-key"
        body = json.loads(request.content)
        assert body["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
        assert body["max_tokens"] == 4096 and body["stream"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "response_format" not in body and "usage" not in body and "reasoning" not in body
        event = {"model": body["model"], "choices": [{"delta": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    provider = NvidiaProvider(settings.model_copy(update={"nvidia_api_key": "fake-nvidia-key"}), client=httpx.Client(transport=httpx.MockTransport(respond)))
    response = provider.complete(LLMRequest("test", "system", "user", {}))
    assert json.loads(response.text)["ok"]
    assert "fake-nvidia-key" not in provider._redact("fake-nvidia-key")


def test_nvidia_errors_identify_provider_and_refusal(settings):
    provider = NvidiaProvider(settings)
    assert isinstance(provider._to_error(401, "private"), LLMConfigurationError)
    assert isinstance(provider._to_error(410, "retired"), LLMConfigurationError)
    assert provider._to_error(402, "private").code == "llm_quota_exceeded"
    assert isinstance(provider._to_error(403, "moderation"), LLMRefusalError)
    with pytest.raises(LLMError, match="NVIDIA") as error:
        provider._to_response(LLMRequest("test", "system", "user", {}), {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
    assert "OPENROUTER" not in error.value.message


def test_nvidia_factory_supports_primary_and_fallback(settings, monkeypatch):
    monkeypatch.setattr(settings, "nvidia_api_key", "fake-nvidia-key")
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    set_provider_override(None)
    assert get_provider().name == "nvidia"
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "fake-openrouter-key")
    monkeypatch.setattr(settings, "llm_fallback_providers", "nvidia")
    set_provider_override(None)
    assert [p.name for p in get_provider().provider.providers] == ["openrouter", "nvidia"]
