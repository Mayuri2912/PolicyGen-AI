"""Check the OpenRouter configuration in backend/.env, step by step.

From the project root:

    backend\\.venv\\Scripts\\python.exe scripts\\check_openrouter.py
    backend\\.venv\\Scripts\\python.exe scripts\\check_openrouter.py --skip-call

It reports, without ever printing the API key:

    1. what the application reads from backend/.env
    2. whether OpenRouter accepts the key, and the account's limits
    3. whether the configured model exists and what it supports
    4. one small structured request through the application's own provider
       (uses one request of the account's quota; --skip-call leaves it out)

Only a short fixed test sentence is sent to OpenRouter, never a document.
The exit code is 0 when every step that ran succeeded.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.core.config import ENV_FILE, get_settings  # noqa: E402
from app.schemas.analysis import llm_json_schema  # noqa: E402
from app.services.ai_analysis.providers.base import LLMError, LLMRequest  # noqa: E402
from app.services.ai_analysis.providers.factory import resolve_provider_name  # noqa: E402
from app.services.ai_analysis.providers.openrouter_provider import OpenRouterProvider  # noqa: E402
from app.services.ai_analysis.structured import structured_call  # noqa: E402

BASE = "https://openrouter.ai/api/v1"


class Probe(BaseModel):
    organization: str
    rule_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-call", action="store_true", help="do not send the test request (saves one request of the quota)")
    args = parser.parse_args()
    settings = get_settings()
    key, model = settings.openrouter_api_key, settings.openrouter_model
    failed = False

    print(f"1. Configuration ({ENV_FILE})")
    active = resolve_provider_name(settings)
    print(f"   LLM_PROVIDER                  = {settings.llm_provider}" + ("" if settings.llm_provider != "auto" else f"  (resolves to {active})"))
    print(f"   OPENROUTER_API_KEY            = {'set (' + str(len(key)) + ' characters)' if key else 'NOT SET'}")
    print(f"   OPENROUTER_MODEL              = {model}")
    print(f"   OPENROUTER_MAX_OUTPUT_TOKENS  = {settings.openrouter_max_output_tokens}")
    print(f"   OPENROUTER_STRUCTURED_OUTPUTS = {settings.openrouter_structured_outputs}")
    print(f"   OPENROUTER_REASONING          = {settings.openrouter_reasoning}")
    print(f"   OPENROUTER_CHUNK_MAX_CHARS    = {settings.openrouter_chunk_max_chars}")
    print(f"   OPENROUTER_TIMEOUT_SECONDS    = {settings.openrouter_timeout_seconds or settings.llm_timeout_seconds}")
    if active != "openrouter":
        print(f"   NOTE: the application will use '{active}', not OpenRouter. Set LLM_PROVIDER=openrouter.")
    if not key:
        print("   FAILED: add OPENROUTER_API_KEY=<your key> to backend/.env (create one at https://openrouter.ai/keys).")
        return 1
    if not key.startswith("sk-or-"):
        print("   WARNING: OpenRouter keys start with 'sk-or-'. This looks like a key from another provider.")

    with httpx.Client(timeout=30) as http:
        print("2. API key")
        try:
            answer = http.get(f"{BASE}/key", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            print(f"   FAILED: OpenRouter could not be reached ({type(exc).__name__}). Check the network connection.")
            return 1
        if answer.status_code != 200:
            print(f"   FAILED: OpenRouter rejected the key (HTTP {answer.status_code}). Check OPENROUTER_API_KEY.")
            return 1
        account = answer.json().get("data") or {}
        print(f"   accepted. free tier: {account.get('is_free_tier')}, usage: {account.get('usage')}, "
              f"limit: {account.get('limit')}, remaining: {account.get('limit_remaining')}")

        print("3. Model")
        catalogue = {m["id"]: m for m in http.get(f"{BASE}/models").json().get("data", [])}
        entry = catalogue.get(model)
        if entry is None:
            failed = True
            free = sorted(m for m in catalogue if m.endswith(":free"))
            print(f"   FAILED: '{model}' is not in the OpenRouter catalogue. Free models now: openrouter/free, {', '.join(free)}")
        else:
            supported = entry.get("supported_parameters") or []
            is_free = model == "openrouter/free" or model.endswith(":free")
            print(f"   found. free: {is_free}, context: {entry.get('context_length')} tokens, "
                  f"JSON schema output: {'structured_outputs' in supported}, JSON mode: {'response_format' in supported}")
            if "structured_outputs" not in supported:
                print("   NOTE: no JSON schema output; the application falls back to JSON mode or plain text and validates the result.")
        with_schema = sorted(
            m for m, e in catalogue.items() if m.endswith(":free") and "structured_outputs" in (e.get("supported_parameters") or [])
        )
        print(f"   free models that accept a JSON schema now: {', '.join(with_schema) or 'none'}")
        if model == "openrouter/free":
            print("   NOTE: openrouter/free uses a different model for each request, so speed and quality vary. "
                  "Naming one model in OPENROUTER_MODEL gives steadier results (see README, 'Which model').")

    if args.skip_call or failed:
        print("4. Test request: skipped")
        return 1 if failed else 0

    print("4. Test request through the application's provider")
    provider = OpenRouterProvider(settings)
    request = LLMRequest(
        "connection_check",
        "You extract facts and answer with JSON only.",
        "Text: 'Harbourline Bakery Ltd. has three access rules.' Return the organization name and the number of rules "
        f"as JSON matching this schema: {llm_json_schema(Probe)}",
        llm_json_schema(Probe),
    )
    started = time.monotonic()
    try:
        result, response = structured_call(provider, request, Probe, repair_attempts=settings.llm_json_repair_attempts)
    except LLMError as exc:
        print(f"   FAILED ({exc.code}) after {time.monotonic() - started:.0f} s: {exc.message}")
        return 1
    print(f"   OK in {time.monotonic() - started:.0f} s. answered by: {response.model}, tokens in/out: "
          f"{response.input_tokens}/{response.output_tokens}, response format used: {provider._format}")
    print(f"   parsed answer: organization={result.organization!r}, rule_count={result.rule_count}")
    print("OpenRouter is working. Restart is not needed: the server re-reads backend/.env when it changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
