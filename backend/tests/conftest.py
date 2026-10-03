"""Shared test setup.

The environment is fixed *before* the application is imported, so the tests
never read the developer's ``.env``, never touch the real data directory and
never call a real LLM (the ``live`` tests opt in explicitly).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values

_DATA_DIR = Path(tempfile.mkdtemp(prefix="policygen_tests_"))
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _live_setting(name: str, default: str = "") -> str:
    """A value for the opt-in live tests: the environment first, then backend/.env."""
    return os.environ.get(name) or (dotenv_values(_ENV_FILE).get(name) if _ENV_FILE.exists() else "") or default


_LIVE_KEY = _live_setting("ANTHROPIC_API_KEY")
_LIVE_GEMINI_KEY = _live_setting("GEMINI_API_KEY")
_LIVE_GEMINI_MODEL = _live_setting("GEMINI_MODEL", "gemini-3.8-flash")
_LIVE_OPENROUTER_KEY = _live_setting("OPENROUTER_API_KEY")
_LIVE_OPENROUTER_MODEL = _live_setting("OPENROUTER_MODEL", "openrouter/free")
os.environ.update(
    {
        # Point the settings at a file that does not exist: the tests must not
        # pick up real keys or overrides from the developer's backend/.env.
        "POLICYGEN_ENV_FILE": str(_DATA_DIR / "no-such.env"),
        "DATA_DIR": str(_DATA_DIR),
        "DATABASE_URL": "",
        "LLM_PROVIDER": "mock",
        "ANTHROPIC_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GEMINI_MODEL": "gemini-3.8-flash",
        "OPENROUTER_API_KEY": "",
        "OPENROUTER_MODEL": "openrouter/free",
        "JOB_EXECUTION_MODE": "inline",
        "RATE_LIMIT_PER_MINUTE": "0",
        "RATE_LIMIT_HEAVY_PER_MINUTE": "0",
        "API_ACCESS_TOKEN": "",
        "RETENTION_HOURS": "0",
        "OCR_ENGINE": "auto",
        "POLICY_TEMPLATES_DIR": "",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import Base, get_engine  # noqa: E402
from app.main import app, rate_limiter  # noqa: E402
from app.services.ai_analysis.providers.base import LLMProvider, LLMRequest, LLMResponse  # noqa: E402
from app.services.ai_analysis.providers.factory import set_provider_override  # noqa: E402
from app.services.ai_analysis.providers.mock_provider import MockProvider  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND.parent / "sample_files"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_sessionfinish(session, exitstatus):
    get_engine().dispose()
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def live_api_key() -> str:
    return _LIVE_KEY


@pytest.fixture(scope="session")
def live_gemini() -> tuple[str, str]:
    """(api key, model) for the live Gemini test; the key is empty when not configured."""
    return _LIVE_GEMINI_KEY, _LIVE_GEMINI_MODEL


@pytest.fixture(scope="session")
def live_openrouter() -> tuple[str, str]:
    """(api key, model) for the live OpenRouter test; the key is empty when not configured."""
    return _LIVE_OPENROUTER_KEY, _LIVE_OPENROUTER_MODEL


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts with an empty database and empty storage."""
    settings = get_settings()
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    for directory in (settings.uploads_dir, settings.extracted_dir, settings.reports_dir, settings.tmp_dir, settings.cache_dir):
        for path in directory.glob("*"):
            if path.is_file():
                path.unlink()
    rate_limiter.reset()
    set_provider_override(None)
    yield
    set_provider_override(None)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def settings():
    return get_settings()


# ----------------------------------------------------------------- helpers
def sample(name: str) -> Path:
    path = SAMPLES / name
    assert path.exists(), f"missing sample file {name}; run scripts/create_sample_files.py"
    return path


def upload(client: TestClient, *items, expect_status: int = 200) -> list[dict]:
    """Upload files.  Each item is a Path or a (name, bytes[, mime]) tuple."""
    files = []
    for item in items:
        if isinstance(item, Path):
            files.append(("files", (item.name, item.read_bytes(), "application/octet-stream")))
        else:
            name, data, *mime = item
            files.append(("files", (name, data, mime[0] if mime else "application/octet-stream")))
    response = client.post("/api/upload", files=files)
    assert response.status_code == expect_status, response.text
    return response.json()["results"] if expect_status == 200 else response.json()


def upload_ok(client: TestClient, *items) -> list[str]:
    """Upload files that must all be accepted and extracted; return their ids."""
    results = upload(client, *items)
    ids = []
    for result in results:
        assert result["accepted"], result
        assert result["file"]["status"] == "extracted", result["file"]
        ids.append(result["file"]["id"])
    return ids


def analyze(client: TestClient, file_ids: list[str]) -> dict:
    response = client.post("/api/analyses", json={"file_ids": file_ids})
    assert response.status_code == 202, response.text
    analysis = client.get(f"/api/analyses/{response.json()['analysis_id']}").json()
    assert analysis["completed"], analysis
    return analysis


def generate(client: TestClient, analysis_id: str, **config) -> dict:
    config.setdefault("policy_type", "information_security")
    response = client.post("/api/policies/generate", json={"analysis_id": analysis_id, "config": config})
    assert response.status_code == 202, response.text
    return client.get(f"/api/reports/{response.json()['report_id']}").json()


def assert_policy_structure(report: dict, forbidden_names: tuple[str, ...] = ()) -> None:
    """The structure every generated policy must have, whichever provider wrote it."""
    sections = [s for s in report["document"]["sections"] if s["kind"] == "section"]
    titles = [s["title"] for s in sections]
    assert titles[:3] == ["Purpose", "Abbreviations", "Scope"], titles
    assert "Executive Summary" not in titles
    assert [s["number"] for s in sections] == [str(n) for n in range(1, len(sections) + 1)]
    listing = next(s for s in sections if s["id"] == "abbreviations")
    tables = [e for e in listing["elements"] if e["type"] == "table"]
    assert all(t["columns"] == ["Abbreviation", "Full Form / Description"] for t in tables)
    body = json.dumps([s["elements"] for s in sections if s["id"] != "references"], ensure_ascii=False)
    for name in forbidden_names:
        assert name not in body, f"the policy names an individual: {name}"


def all_text(report: dict) -> str:
    """Every piece of text in a report's document model."""
    return json.dumps(report["document"], ensure_ascii=False)


class ScriptedProvider(LLMProvider):
    """A provider that replays scripted responses.

    Each script entry is a string (returned as the model text), a dict
    (JSON-encoded), an exception (raised), or the string "MOCK" (delegates to
    the rule-based mock).  When the script runs out it delegates to the mock.
    """

    name = "scripted"
    model = "scripted-test-model"
    simulated = False

    def __init__(self, script=None, by_task=None):
        self.script = list(script or [])
        self.by_task = {task: list(entries) for task, entries in (by_task or {}).items()}
        self.requests: list[LLMRequest] = []
        self._mock = MockProvider()

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.task in self.by_task:
            queue = self.by_task[request.task]
            entry = queue.pop(0) if queue else "MOCK"
        elif self.script:
            entry = self.script.pop(0)
        else:
            entry = "MOCK"
        if isinstance(entry, Exception):
            raise entry
        if entry == "MOCK":
            return LLMResponse(text=self._mock.complete(request).text, model=self.model)
        if callable(entry):
            entry = entry(request, json.loads(self._mock.complete(request).text))
        if isinstance(entry, dict):
            entry = json.dumps(entry)
        return LLMResponse(text=entry, model=self.model)

    def tasks(self) -> list[str]:
        return [request.task for request in self.requests]


@pytest.fixture
def scripted():
    def install(script=None, by_task=None) -> ScriptedProvider:
        provider = ScriptedProvider(script, by_task)
        set_provider_override(provider)
        return provider

    return install


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, text: str):
        self.text = text

    def image_to_text(self, image) -> str:
        return self.text


@pytest.fixture
def fake_ocr(monkeypatch):
    """Install an OCR engine that returns fixed text (Tesseract is not required)."""

    def install(text: str | None):
        engine = FakeOcr(text) if text is not None else None
        for module in ("image", "pdf"):
            monkeypatch.setattr(f"app.services.file_processing.parsers.{module}.get_ocr_engine", lambda engine=engine: engine)
        return engine

    return install


def fake_message(text: str, stop_reason: str = "end_turn"):
    """An object shaped like the SDK's final Message."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


class FakeAnthropicClient:
    """Stands in for ``anthropic.Anthropic``: replays results or raises errors."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        namespace = SimpleNamespace(stream=self._stream)
        self.messages = namespace
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        class _Manager:
            def __enter__(self_inner):
                return SimpleNamespace(get_final_message=lambda: outcome)

            def __exit__(self_inner, *exc):
                return False

        return _Manager()
