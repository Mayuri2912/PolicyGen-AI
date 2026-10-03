"""Provider selection.

LLM_PROVIDER=auto (default) uses Anthropic when ANTHROPIC_API_KEY is set, else
Gemini when GEMINI_API_KEY is set, else OpenRouter when OPENROUTER_API_KEY is
set, and the simulated mock otherwise, so the app runs out of the box.
``anthropic``, ``gemini``, ``openrouter`` and ``mock`` select a provider
explicitly; an explicit choice never falls back to another provider, it
reports what is missing instead.  To add a vendor, write an ``LLMProvider`` subclass and add it to
``_BUILDERS``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

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
    from app.services.ai_analysis.providers.openrouter_provider import OpenRouterProvider

    return OpenRouterProvider(settings)


_BUILDERS: dict[str, Callable[[Settings], LLMProvider]] = {
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
}


_PROVIDER_FIELDS = ("anthropic_", "gemini_", "openrouter_", "llm_")


def resolve_provider_name(settings: Settings) -> str:
    if settings.llm_provider == "auto":
        if settings.anthropic_api_key:
            return "anthropic"
        if settings.gemini_api_key:
            return "gemini"
        if settings.openrouter_api_key:
            return "openrouter"
        return "mock"
    return settings.llm_provider


def _configured_model(name: str, settings: Settings) -> str:
    return {
        "anthropic": settings.anthropic_model,
        "gemini": settings.gemini_model,
        "openrouter": settings.openrouter_model,
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
            _cached = (key, builder(settings))
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
        "Set ANTHROPIC_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY to use a real model."
        if provider.simulated
        else f"Documents are sent to the {provider.service_name or provider.name} for analysis and policy generation."
    )
    return info
