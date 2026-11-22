"""Provider selection.

LLM_PROVIDER=auto (default) uses Anthropic when ANTHROPIC_API_KEY is set, else
Gemini when GEMINI_API_KEY is set, else OpenRouter when OPENROUTER_API_KEY is
set, and the simulated mock otherwise, so the app runs out of the box.
``anthropic``, ``gemini``, ``openrouter``, ``groq``, ``nvidia`` and ``mock`` select a provider
explicitly; additional providers can be configured with LLM_FALLBACK_PROVIDERS; otherwise it
reports what is missing instead.  To add a vendor, write an ``LLMProvider`` subclass and add it to
``_BUILDERS``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings, get_llm_settings
from app.services.ai_analysis.providers.base import LLMConfigurationError, LLMProvider
from app.services.ai_analysis.providers.mock_provider import MockProvider

_lock = threading.Lock()
_cached: tuple[tuple, LLMProvider] | None = None
_override: LLMProvider | None = None


def anthropic_credentials_present(settings: Settings) -> bool:
    """True when the SDK has something to authenticate with.

    The SDK also accepts ANTHROPIC_AUTH_TOKEN and a profile created by the
    ``ant`` CLI, so those count as configured.
    """
    return bool(
        settings.anthropic_api_key
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or (Path.home() / ".config" / "anthropic").is_dir()
    )


def _build_anthropic(settings: Settings) -> LLMProvider:
    if not anthropic_credentials_present(settings):
        raise LLMConfigurationError(
            "No Anthropic API key is configured. Set ANTHROPIC_API_KEY in backend/.env, or set LLM_PROVIDER=mock "
            "to use the simulated mode."
        )
    from app.services.ai_analysis.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(settings)


def _build_gemini(settings: Settings) -> LLMProvider:
    if not settings.gemini_api_key:
        raise LLMConfigurationError(
            "No Gemini API key is configured. Set GEMINI_API_KEY in backend/.env, or set LLM_PROVIDER=mock "
            "to use the simulated mode."
        )
    try:
        # Imported here so the application still starts when the optional SDK is missing.
        from app.services.ai_analysis.providers.gemini_provider import GeminiProvider
    except ImportError as exc:
        raise LLMConfigurationError(
            "The Gemini SDK is not installed. Run: pip install -r requirements.txt"
        ) from exc

    return GeminiProvider(settings)


def _build_openrouter(settings: Settings) -> LLMProvider:
    if not settings.openrouter_api_key:
        raise LLMConfigurationError(
            "No OpenRouter API key is configured. Set OPENROUTER_API_KEY in backend/.env, or set LLM_PROVIDER=mock "
            "to use the simulated mode."
        )
    if not settings.openrouter_model.strip():
        raise LLMConfigurationError("No OpenRouter model is configured. Set OPENROUTER_MODEL in backend/.env.")
    from app.services.ai_analysis.providers.openrouter_provider import (
        OpenRouterProvider,
    )

    return OpenRouterProvider(settings)


def _build_groq(settings: Settings) -> LLMProvider:
    if not settings.groq_api_key or not settings.groq_model:
        raise LLMConfigurationError("Set GROQ_API_KEY and GROQ_MODEL to use Groq.")
    from app.services.ai_analysis.providers.groq_provider import GroqProvider
    return GroqProvider(settings)


def _build_nvidia(settings: Settings) -> LLMProvider:
    if not settings.nvidia_api_key or not settings.nvidia_model:
        raise LLMConfigurationError("Set NVIDIA_API_KEY and NVIDIA_MODEL to use NVIDIA NIM.")
    from app.services.ai_analysis.providers.nvidia_provider import NvidiaProvider
    return NvidiaProvider(settings)


_BUILDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "nvidia": _build_nvidia,
    "groq": _build_groq,
    "anthropic": _build_anthropic,
    "gemini": _build_gemini,
    "openrouter": _build_openrouter,
    "mock": lambda _settings: MockProvider(),
}

# Names shown in the UI for each provider id, including for analyses and
# reports made earlier with a provider that is no longer the active one.
PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "mock": "Mock mode",
    "groq": "Groq",
    "nvidia": "NVIDIA NIM",
}


_PROVIDER_FIELDS = ("anthropic_", "gemini_", "openrouter_", "groq_", "nvidia_", "llm_")


def resolve_provider_name(settings: Settings) -> str:
    if settings.llm_provider == "auto":
        if settings.anthropic_api_key or settings.anthropic_api_keys:
            return "anthropic"
        if settings.gemini_api_key or settings.gemini_api_keys or settings.gemini_api_key2:
            return "gemini"
        if settings.openrouter_api_key or settings.openrouter_api_keys or settings.openrouter_api_key2 or settings.openrouter_api_key3:
            return "openrouter"
        if settings.groq_api_key or settings.groq_api_keys:
            return "groq"
        if settings.nvidia_api_key or settings.nvidia_api_keys:
            return "nvidia"
        return "mock"
    return settings.llm_provider


def _configured_model(name: str, settings: Settings) -> str:
    return {
        "anthropic": settings.anthropic_model,
        "gemini": settings.gemini_model,
        "openrouter": settings.openrouter_model,
        "groq": settings.groq_model,
        "nvidia": settings.nvidia_model,
    }.get(name, "")


def get_provider() -> LLMProvider:
    """Return the configured provider, or raise ``LLMConfigurationError``."""
    global _cached
    if _override is not None:
        return _override
    settings = get_llm_settings()
    name = resolve_provider_name(settings)
    # A changed key, model or limit rebuilds the provider.
    key = (name, *(value for field, value in settings.model_dump().items() if field.startswith(_PROVIDER_FIELDS)))
    with _lock:
        if _cached is None or _cached[0] != key:
            builder = _BUILDERS.get(name)
            if builder is None:
                raise LLMConfigurationError(
                    f"Unknown LLM_PROVIDER '{name}'. Supported values: auto, {', '.join(sorted(_BUILDERS))}."
                )
            names = list(dict.fromkeys([name, *[n.strip().lower() for n in settings.llm_fallback_providers.split(",") if n.strip()]]))
            if name == "mock":
                names = ["mock"]
            providers = []
            for candidate in names:
                if candidate == "mock" and name != "mock":
                    raise LLMConfigurationError("Mock mode cannot be used as a production fallback.")
                candidate_builder = _BUILDERS.get(candidate)
                if candidate_builder is None:
                    raise LLMConfigurationError(f"Unknown fallback provider '{candidate}'.")
                singular = f"{candidate}_api_key"
                pool = getattr(settings, f"{candidate}_api_keys", "")
                numbered = [getattr(settings, f"{candidate}_api_key{slot}", "") for slot in (2, 3)]
                keys = list(dict.fromkeys(k.strip().strip("\"'") for k in [getattr(settings, singular, ""), *numbered, *pool.split(",")] if k.strip()))
                if keys:
                    providers.extend(candidate_builder(settings.model_copy(update={singular: credential, "llm_max_retries": 0 if len(names) > 1 or len(keys) > 1 else settings.llm_max_retries})) for credential in keys)
                elif candidate == name and len(names) == 1:
                    providers.append(candidate_builder(settings))
            if not providers:
                raise LLMConfigurationError("No API credentials are configured for the selected providers.")
            from app.services.ai_analysis.providers.fallback import (
                FallbackProvider,
                RetryingProvider,
            )
            provider = FallbackProvider(providers, settings.llm_key_cooldown_seconds) if len(providers) > 1 else providers[0]
            if not provider.simulated and settings.llm_auto_retries > 0:
                provider = RetryingProvider(provider, settings.llm_auto_retries, settings.llm_auto_retry_delay_seconds)
            _cached = (key, provider)
        return _cached[1]


def set_provider_override(provider: LLMProvider | None) -> None:
    """Force a specific provider instance (used by the automated tests)."""
    global _override, _cached
    _override = provider
    _cached = None


def provider_status() -> dict:
    """Describe the active provider for the health endpoint, without raising.

    Only names and the model id are returned; API keys never are.
    """
    settings = get_llm_settings()
    name = resolve_provider_name(settings)
    try:
        provider = get_provider()
    except LLMConfigurationError as exc:
        return {
            "provider": name,
            "display_name": PROVIDER_LABELS.get(name, name),
            "model": _configured_model(name, settings),
            "simulated": False,
            "service_name": "",
            "ready": False,
            "message": exc.message,
        }
    info = provider.describe()
    info["ready"] = True
    info["message"] = (
        "Mock mode: results are produced by keyword rules, not by an AI model. "
        "Set ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY or GROQ_API_KEY to use a real model."
        if provider.simulated
        else f"Documents are sent to the {provider.service_name or provider.name} for analysis and policy generation."
    )
    return info
