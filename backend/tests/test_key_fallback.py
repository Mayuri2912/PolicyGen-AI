from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from app.services.ai_analysis.providers.base import (
    LLMProvider,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)
from app.services.ai_analysis.providers.fallback import FallbackProvider
from app.services.ai_analysis.structured import structured_call


def provider(*results):
    stub = Mock(spec=LLMProvider)
    stub.name = "openrouter"
    stub.model = "openrouter/free"
    stub.display_name = "OpenRouter"
    stub.service_name = "OpenRouter"
    stub.simulated = False
    stub.chunk_max_chars = 5000
    stub.complete.side_effect = results
    return stub


REQUEST = LLMRequest("test", "system", "original", {})


def test_slow_provider_is_skipped_for_following_batches(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.monotonic", lambda: clock[0])
    first = provider(LLMTimeoutError("slow"), LLMResponse("{}", "recovered"))
    second = provider(LLMResponse("{}", "fast"), LLMResponse("{}", "fast"))
    pool = FallbackProvider([first, second])
    assert pool.complete(REQUEST).model == "fast"
    clock[0] += 60
    assert pool.complete(REQUEST).model == "fast"
    assert first.complete.call_count == 1
    clock[0] += 241
    assert pool.complete(REQUEST).model == "recovered"


def test_rate_limit_falls_back_and_keeps_successful_key():
    first = provider(LLMRateLimitError("quota"))
    second = provider(LLMResponse("{}", "model"), LLMResponse("{}", "model"))
    pool = FallbackProvider([first, second])
    pool.complete(REQUEST)
    pool.complete(REQUEST)
    assert first.complete.call_count == 1
    assert second.complete.call_count == 2
    assert second.complete.call_args.args[0] is REQUEST


def test_exhaustion_does_not_repeat_calls():
    first = provider(LLMRateLimitError("quota"))
    second = provider(LLMRateLimitError("quota"))
    pool = FallbackProvider([first, second])
    for _ in range(2):
        with pytest.raises(LLMRateLimitError):
            pool.complete(REQUEST)
    assert first.complete.call_count == second.complete.call_count == 1


def test_refusal_does_not_fall_back():
    second = provider(LLMResponse("{}", "model"))
    with pytest.raises(LLMRefusalError):
        FallbackProvider([provider(LLMRefusalError("blocked")), second]).complete(REQUEST)
    second.complete.assert_not_called()


def test_invalid_json_moves_to_second_key_after_repair():
    class Result(BaseModel):
        value: int
    first = provider(LLMResponse("broken", "model"), LLMResponse("still broken", "model"))
    second = provider(LLMResponse('{"value": 3}', "model"))
    result, _ = structured_call(FallbackProvider([first, second]), REQUEST, Result)
    assert result.value == 3
    assert second.complete.call_args.args[0] is REQUEST


def test_automatic_retry_is_delayed_and_bounded(monkeypatch):
    from app.services.ai_analysis.providers.fallback import RetryingProvider
    sleeps = []
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.sleep", sleeps.append)
    stub = provider(LLMRateLimitError("busy"), LLMRateLimitError("busy"), LLMResponse("{}", "recovered"))
    assert RetryingProvider(stub, retries=2, delay=5).complete(REQUEST).model == "recovered"
    assert sleeps == [1] * 15
    assert stub.complete.call_count == 3


def test_automatic_retry_exhaustion_and_refusal(monkeypatch):
    from app.services.ai_analysis.providers.fallback import RetryingProvider
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.sleep", lambda _: None)
    stub = provider(*[LLMRateLimitError("busy") for _ in range(3)])
    with pytest.raises(LLMRateLimitError):
        RetryingProvider(stub).complete(REQUEST)
    assert stub.complete.call_count == 3
    blocked = provider(LLMRefusalError("blocked"))
    with pytest.raises(LLMRefusalError):
        RetryingProvider(blocked).complete(REQUEST)
    assert blocked.complete.call_count == 1


def test_provider_failover_uses_same_request():
    first = provider(LLMRateLimitError("quota"))
    first.name = "gemini"
    second = provider(LLMResponse("{}", "openrouter/free"))
    pool = FallbackProvider([first, second])
    assert pool.complete(REQUEST).model == "openrouter/free"
    assert second.complete.call_args.args[0] is REQUEST


def test_cancel_during_retry_stops_calls(monkeypatch):
    from app.services.ai_analysis.providers.fallback import RetryingProvider
    from app.services.jobs import JobCancelled
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.sleep", lambda _: None)
    stub = provider(LLMRateLimitError("busy"))
    request = LLMRequest("test", "system", "original", {}, observer=Mock(side_effect=JobCancelled))
    with pytest.raises(JobCancelled):
        RetryingProvider(stub).complete(request)
    assert stub.complete.call_count == 1


def test_factory_builds_gemini_then_openrouter(settings, monkeypatch):
    from app.services.ai_analysis.providers import factory
    from app.services.ai_analysis.providers.fallback import RetryingProvider
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-openrouter")
    monkeypatch.setattr(settings, "llm_fallback_providers", "openrouter")
    first = provider(LLMRateLimitError("busy"))
    second = provider(LLMResponse("{}", "backup"))
    monkeypatch.setitem(factory._BUILDERS, "gemini", lambda _: first)
    monkeypatch.setitem(factory._BUILDERS, "openrouter", lambda _: second)
    factory.set_provider_override(None)
    selected = factory.get_provider()
    assert isinstance(selected, RetryingProvider)
    assert selected.provider.providers == [first, second]
    assert selected.complete(REQUEST).model == "backup"
    monkeypatch.setattr(settings, "gemini_api_key", "")
    factory.set_provider_override(None)
    assert factory.get_provider().provider is second


def test_outer_retry_never_bypasses_exhausted_key_cooldowns(monkeypatch):
    from app.services.ai_analysis.providers.fallback import RetryingProvider
    first = provider(LLMRateLimitError("daily", code="llm_quota_exceeded", retry_after_seconds=3600))
    second = provider(LLMRateLimitError("daily", code="llm_quota_exceeded", retry_after_seconds=7200))
    sleep = Mock()
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.sleep", sleep)
    pool = FallbackProvider([first, second])
    with pytest.raises(LLMRateLimitError):
        RetryingProvider(pool).complete(REQUEST)
    assert first.complete.call_count == second.complete.call_count == 1
    sleep.assert_not_called()
    with pytest.raises(LLMRateLimitError):
        pool.complete(REQUEST)
    assert first.complete.call_count == second.complete.call_count == 1


def test_invalid_key_disabled_until_configuration_changes():
    from app.services.ai_analysis.providers.base import LLMConfigurationError
    first = provider(LLMConfigurationError("invalid"))
    second = provider(LLMResponse("{}", "ok"), LLMResponse("{}", "ok"))
    pool = FallbackProvider([first, second])
    pool.complete(REQUEST)
    pool.complete(REQUEST)
    assert first.complete.call_count == 1
    assert pool.describe()["key_status"][0]["retry_after_seconds"] is None


def test_truncated_answer_does_not_cool_down_key_for_smaller_batch():
    from app.services.ai_analysis.providers.base import LLMError
    first = provider(LLMError("truncated", code="llm_output_truncated"), LLMResponse("{}", "small"))
    second = provider(LLMRateLimitError("daily", code="llm_quota_exceeded"))
    pool = FallbackProvider([first, second])
    with pytest.raises(LLMError) as error:
        pool.complete(REQUEST)
    # Prefer the request-size failure so consolidation can split its batch.
    assert error.value.code == "llm_output_truncated"
    assert pool.complete(REQUEST).model == "small"


def test_factory_disables_nested_sdk_retries(settings, monkeypatch):
    from app.services.ai_analysis.providers import factory
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test")
    monkeypatch.setattr(settings, "openrouter_api_key", "test")
    monkeypatch.setattr(settings, "llm_fallback_providers", "openrouter")
    builder = Mock(side_effect=lambda s: provider(LLMResponse("{}", "ok")))
    monkeypatch.setitem(factory._BUILDERS, "gemini", builder)
    monkeypatch.setitem(factory._BUILDERS, "openrouter", builder)
    factory.set_provider_override(None)
    factory.get_provider()
    assert all(call.args[0].llm_max_retries == 0 for call in builder.call_args_list)


def test_second_openrouter_key_precedes_gemini_and_groq(settings, monkeypatch):
    from app.services.ai_analysis.providers import factory
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "first")
    monkeypatch.setattr(settings, "openrouter_api_key2", "second")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini")
    monkeypatch.setattr(settings, "groq_api_key", "groq")
    monkeypatch.setattr(settings, "llm_fallback_providers", "gemini,groq")
    built = []
    def build(name):
        def create(s):
            built.append((name, getattr(s, name + "_api_key")))
            stub = provider(LLMResponse("{}", name))
            stub.name = name
            return stub
        return create
    for name in ("openrouter", "gemini", "groq"):
        monkeypatch.setitem(factory._BUILDERS, name, build(name))
    factory.set_provider_override(None)
    selected = factory.get_provider()
    assert built == [("openrouter", "first"), ("openrouter", "second"), ("gemini", "gemini"), ("groq", "groq")]
    assert len(selected.provider.providers) == 4


def test_preferred_key_is_used_again_after_its_cooldown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.services.ai_analysis.providers.fallback.time.monotonic", lambda: clock[0])
    first = provider(LLMRateLimitError("limited", retry_after_seconds=2), LLMResponse("{}", "preferred"))
    second = provider(LLMResponse("{}", "backup"))
    pool = FallbackProvider([first, second])
    assert pool.complete(REQUEST).model == "backup"
    clock[0] += 3
    assert pool.complete(REQUEST).model == "preferred"
    assert second.complete.call_count == 1


@pytest.mark.parametrize("primary", ["gemini", "auto"])
def test_second_gemini_key_works_without_first(settings, monkeypatch, primary):
    from app.core.config import Settings
    from app.services.ai_analysis.providers import factory
    second = Settings(_env_file=None, gemini_api_key2="  'second'  ").gemini_api_key2
    assert second == "second"
    monkeypatch.setattr(settings, "llm_provider", primary)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key2", second)
    monkeypatch.setattr(settings, "llm_fallback_providers", "")
    builder = Mock(side_effect=lambda s: provider(LLMResponse("{}", "ok")))
    monkeypatch.setitem(factory._BUILDERS, "gemini", builder)
    factory.set_provider_override(None)
    assert factory.get_provider().complete(REQUEST).model == "ok"
    assert builder.call_args.args[0].gemini_api_key == "second"


def test_second_gemini_key_failover_and_deduplication(settings, monkeypatch):
    from app.services.ai_analysis.providers import factory
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "first")
    monkeypatch.setattr(settings, "gemini_api_key2", "second")
    monkeypatch.setattr(settings, "gemini_api_keys", "second,third")
    monkeypatch.setattr(settings, "llm_fallback_providers", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "groq")
    built = []
    def build(name):
        def create(s):
            credential = getattr(s, name + "_api_key")
            built.append((name, credential))
            result = LLMRateLimitError("exhausted", retry_after_seconds=3600) if credential == "first" else LLMResponse("{}", credential)
            stub = provider(result)
            stub.name = name
            return stub
        return create
    for name in ("gemini", "groq"):
        monkeypatch.setitem(factory._BUILDERS, name, build(name))
    factory.set_provider_override(None)
    selected = factory.get_provider()
    assert built == [("gemini", "first"), ("gemini", "second"), ("gemini", "third"), ("groq", "groq")]
    assert selected.complete(REQUEST).model == "second"
    assert selected.provider.providers[0].complete.call_count == 1


@pytest.mark.parametrize("primary", ["openrouter", "auto"])
def test_third_openrouter_key_works_without_previous_keys(settings, monkeypatch, primary):
    from app.core.config import Settings
    from app.services.ai_analysis.providers import factory
    third = Settings(_env_file=None, openrouter_api_key3="  'third'  ").openrouter_api_key3
    assert third == "third"
    monkeypatch.setattr(settings, "llm_provider", primary)
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key2", "")
    monkeypatch.setattr(settings, "openrouter_api_key3", third)
    monkeypatch.setattr(settings, "llm_fallback_providers", "")
    builder = Mock(side_effect=lambda s: provider(LLMResponse("{}", "ok")))
    monkeypatch.setitem(factory._BUILDERS, "openrouter", builder)
    factory.set_provider_override(None)
    assert factory.get_provider().complete(REQUEST).model == "ok"
    assert builder.call_args.args[0].openrouter_api_key == "third"


def test_third_openrouter_key_is_used_before_other_providers(settings, monkeypatch):
    from app.services.ai_analysis.providers import factory
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "llm_fallback_providers", "gemini,groq,nvidia")
    for field, value in {"openrouter_api_key":"first", "openrouter_api_key2":"second", "openrouter_api_key3":"third", "openrouter_api_keys":"third,fourth", "gemini_api_key":"gemini1", "gemini_api_key2":"gemini2", "groq_api_key":"groq", "nvidia_api_key":"nvidia"}.items():
        monkeypatch.setattr(settings, field, value)
    built = []
    def build(name):
        def create(s):
            credential = getattr(s, name + "_api_key")
            built.append((name, credential))
            result = LLMRateLimitError("exhausted", retry_after_seconds=3600) if credential in ("first", "second") else LLMResponse("{}", credential)
            stub = provider(result)
            stub.name = name
            return stub
        return create
    for name in ("openrouter", "gemini", "groq", "nvidia"):
        monkeypatch.setitem(factory._BUILDERS, name, build(name))
    factory.set_provider_override(None)
    selected = factory.get_provider()
    assert built == [("openrouter", "first"), ("openrouter", "second"), ("openrouter", "third"), ("openrouter", "fourth"), ("gemini", "gemini1"), ("gemini", "gemini2"), ("groq", "groq"), ("nvidia", "nvidia")]
    assert selected.complete(REQUEST).model == "third"
    assert selected.provider.providers[3].complete.call_count == 0
    assert selected.provider.providers[4].complete.call_count == 0
