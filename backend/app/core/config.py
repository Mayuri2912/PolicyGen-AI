"""Application configuration.

Every tunable lives here and is read from environment variables (or
``backend/.env``), so the whole runtime contract is visible in one place and
mirrored in ``.env.example``.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"

# The settings file.  POLICYGEN_ENV_FILE points it elsewhere; the automated
# tests use that so they never read a developer's real keys from backend/.env.
ENV_FILE = Path(os.environ.get("POLICYGEN_ENV_FILE") or BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General -------------------------------------------------------------
    app_name: str = "PolicyGen AI"
    environment: str = "development"
    log_level: str = "INFO"
    data_dir: Path = BACKEND_DIR / "data"
    # Empty means "SQLite file inside data_dir".  Any SQLAlchemy URL works, e.g.
    # postgresql+psycopg://user:pass@host/dbname
    database_url: str = ""
    database_schema: str = "public"
    storage_backend: str = "local"
    supabase_url: str = ""
    supabase_database_url: str = ""
    supabase_service_key: str = ""
    supabase_storage_bucket: str = "policygen-files"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://policygen-workspace.onrender.com"
    # Optional shared secret.  When set, every /api request must send it as
    # "Authorization: Bearer <token>".
    api_access_token: str = ""
    public_access: bool = False

    # --- Upload and extraction limits ----------------------------------------
    max_upload_mb: int = 25
    max_files_per_upload: int = 10
    max_extracted_chars: int = 2_000_000
    max_pdf_pages: int = 500
    max_table_rows: int = 5000
    max_archive_uncompressed_mb: int = 300
    extraction_timeout_seconds: int = 120

    # --- OCR -----------------------------------------------------------------
    # auto: use Tesseract when installed, otherwise report OCR as unavailable.
    # tesseract: require Tesseract.  claude: transcribe images with the Claude
    # API (sends the image to Anthropic - opt-in only).  none: disable OCR.
    ocr_engine: str = "auto"
    tesseract_cmd: str = ""

    # --- LLM -----------------------------------------------------------------
    # auto: Anthropic when ANTHROPIC_API_KEY is set, else Gemini when
    # GEMINI_API_KEY is set, else OpenRouter when OPENROUTER_API_KEY is set,
    # otherwise the simulated mock.
    # anthropic | gemini | openrouter | groq | mock select a provider explicitly.
    llm_provider: str = "auto"
    # Comma-separated server-side key pools; the singular key remains supported.
    anthropic_api_keys: str = ""
    gemini_api_keys: str = ""
    openrouter_api_keys: str = ""
    groq_api_keys: str = ""
    nvidia_api_keys: str = ""
    nvidia_api_key: str = ""
    nvidia_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    nvidia_max_output_tokens: int = 4096
    nvidia_chunk_max_chars: int = 5000
    nvidia_timeout_seconds: int = 300
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_max_output_tokens: int = 32000
    groq_chunk_max_chars: int = 5000
    groq_timeout_seconds: int = 300
    llm_key_cooldown_seconds: int = 60
    llm_fallback_providers: str = ""
    llm_auto_retries: int = 2
    llm_auto_retry_delay_seconds: int = 5
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "high"
    anthropic_max_output_tokens: int = 32000
    anthropic_structured_outputs: bool = True
    anthropic_refusal_fallback: bool = True
    gemini_api_key: str = ""
    gemini_api_key2: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    # Thinking tokens count towards this limit on Gemini, so it is generous.
    gemini_max_output_tokens: int = 65536
    gemini_structured_outputs: bool = True
    openrouter_api_key: str = ""
    openrouter_api_key2: str = ""
    openrouter_api_key3: str = ""
    # Any model id from openrouter.ai/models.  "openrouter/free" lets OpenRouter
    # pick a free model; ids ending in ":free" name a specific free model.
    openrouter_model: str = "openrouter/free"
    # 0 sends no limit and leaves it to the model.  Consolidating a real
    # document's findings was seen to need more than 16,000 tokens.
    openrouter_max_output_tokens: int = 32000
    openrouter_structured_outputs: bool = True
    # off | low | medium | high | default.  Free models are mostly reasoning
    # models and can spend minutes thinking before they answer; extraction to
    # a fixed JSON structure does not need it, so it is switched off.
    openrouter_reasoning: str = "off"
    # Documents are sent to OpenRouter in parts of at most this many characters
    # (never more than CHUNK_MAX_CHARS).  The findings for a part are several
    # times longer than the part, and free models write slowly, so small parts
    # are what keeps each request inside the output and time limits.
    openrouter_chunk_max_chars: int = 5000
    # Total time allowed for one request, from sending it to the last byte of
    # the answer.  Free models write slowly (measured: 40-120 tokens a second),
    # so this is longer than LLM_TIMEOUT_SECONDS.  0 uses LLM_TIMEOUT_SECONDS.
    openrouter_timeout_seconds: int = 600
    llm_timeout_seconds: int = 300
    llm_max_retries: int = 2
    llm_json_repair_attempts: int = 1
    chunk_max_chars: int = 48_000
    analysis_call_timeout_seconds: int = 180
    consolidation_batch_chars: int = 10000
    max_chunks_per_analysis: int = 40
    quality_correction_rounds: int = 1

    # --- Policy templates ----------------------------------------------------
    # Optional extra directory of policy-type JSON files, loaded in addition
    # to the built-in ones.
    policy_templates_dir: str = ""

    # --- Risk scoring (documented in docs/ARCHITECTURE.md) -------------------
    # Upper bounds of likelihood x impact for Low, Medium and High.  Anything
    # above the last value is Critical.
    risk_level_thresholds: str = "4,9,15"

    # --- Background jobs -----------------------------------------------------
    job_workers: int = 3
    # "thread" runs jobs on a worker pool; "inline" runs them in the request
    # (used by the automated tests for determinism).
    job_execution_mode: str = "thread"

    # --- Rate limiting (requests per minute, per client address) -------------
    rate_limit_per_minute: int = 300
    rate_limit_heavy_per_minute: int = 40

    # --- Privacy and retention -----------------------------------------------
    # 0 keeps data until the user deletes it.
    retention_hours: int = 0
    delete_source_after_extraction: bool = False

    @field_validator("llm_provider", "ocr_engine", "job_execution_mode", "anthropic_effort", "openrouter_reasoning")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    # A key or model id pasted with a stray space, quote or line ending would
    # otherwise be sent as-is and rejected by the provider.
    @field_validator(
        "anthropic_api_key", "anthropic_model", "gemini_api_key", "gemini_api_key2", "gemini_model", "openrouter_api_key", "openrouter_api_key2", "openrouter_api_key3", "openrouter_model", "groq_api_key", "groq_model", "nvidia_api_key", "nvidia_model"
    )
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip().strip("\"'").strip()

    # --- Derived values ------------------------------------------------------
    @property
    def resolved_database_url(self) -> str:
        if self.storage_backend == "supabase":
            url = self.supabase_database_url or self.database_url
            if not url.startswith("postgresql") or "sslmode=require" not in url:
                raise ValueError("Supabase storage requires a PostgreSQL URL with sslmode=require.")
            return url
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'policygen.db').as_posix()}"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "generated_reports"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def risk_thresholds(self) -> tuple[int, int, int]:
        parts = [int(p) for p in self.risk_level_thresholds.split(",") if p.strip()]
        if len(parts) != 3 or sorted(parts) != parts:
            raise ValueError("RISK_LEVEL_THRESHOLDS must be three ascending integers, e.g. 4,9,15")
        return parts[0], parts[1], parts[2]

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.uploads_dir,
            self.extracted_dir,
            self.reports_dir,
            self.tmp_dir,
            self.cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def _env_file_stamp() -> tuple[int, int] | None:
    try:
        stat = ENV_FILE.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


_startup_stamp: tuple[int, int] | None = None
_llm_lock = threading.Lock()
_llm_reloaded: tuple[tuple[int, int] | None, Settings] | None = None


@lru_cache
def get_settings() -> Settings:
    global _startup_stamp
    _startup_stamp = _env_file_stamp()
    settings = Settings()
    settings.ensure_directories()
    return settings


def get_llm_settings() -> Settings:
    """Settings for choosing and calling the AI provider, following edits to ``backend/.env``.

    ``get_settings`` is read once when the server starts.  The provider, its
    key and its model are what people change while the server is running, and
    a server still using the old values looks like a silent fallback to another
    provider.  So when the settings file has changed since startup it is read
    again here; an edit that does not validate keeps the last good values.
    """
    global _llm_reloaded
    base = get_settings()
    stamp = _env_file_stamp()
    if stamp == _startup_stamp:
        return base
    with _llm_lock:
        if _llm_reloaded is None or _llm_reloaded[0] != stamp:
            try:
                fresh = Settings()
            except ValueError as exc:  # pydantic's ValidationError is a ValueError
                # Field names only: the values may be secrets.
                fields = sorted({str(e["loc"][0]) for e in exc.errors()}) if hasattr(exc, "errors") else []
                logging.getLogger("policygen.config").error(
                    "backend/.env changed but is not valid; keeping the previous AI settings", extra={"fields": fields}
                )
                fresh = _llm_reloaded[1] if _llm_reloaded else base
            else:
                logging.getLogger("policygen.config").warning(
                    "backend/.env changed; AI provider settings were re-read", extra={"llm_provider": fresh.llm_provider}
                )
            _llm_reloaded = (stamp, fresh)
        return _llm_reloaded[1]
