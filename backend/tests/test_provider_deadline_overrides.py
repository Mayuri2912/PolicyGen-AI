import pytest

from app.services.ai_analysis.providers.base import LLMRequest, LLMTimeoutError
from app.services.ai_analysis.providers.groq_provider import GroqProvider
from app.services.ai_analysis.providers.nvidia_provider import NvidiaProvider
from app.services.ai_analysis.providers.openrouter_provider import _TimeLimitReached


@pytest.mark.parametrize("provider_class", [GroqProvider, NvidiaProvider])
def test_provider_timeout_remains_retryable_with_request_deadline(settings, monkeypatch, provider_class):
    provider = provider_class(settings.model_copy(update={"llm_max_retries": 0}))

    def timeout(*args):
        raise _TimeLimitReached(181, {"model": "test", "reasoning_chars": 0, "answer_chars": 0})

    monkeypatch.setattr(provider, "_send", timeout)
    with pytest.raises(LLMTimeoutError) as error:
        provider.complete(LLMRequest("consolidate", "system", "user", {}, timeout_seconds=180))
    assert error.value.retryable
