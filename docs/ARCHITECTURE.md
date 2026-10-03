# PolicyGen AI – Technical Architecture

This document describes how the four team modules fit together, the data that
flows between them, the API, the analysis schema and the policy generation
pipeline. For installation and usage see the [README](../README.md).

## 1. System overview

```
 Browser (React + Vite)
        │  /api  (JSON, multipart upload, file download)
        ▼
 FastAPI backend ── ApiGuard middleware (access token, rate limits) ── CORS
        │
        ├── api/                    REST endpoints                       (Mayuri)
        ├── services/jobs.py        background job runner, state machine (Mayuri)
        ├── services/file_processing/   validation, parsers, normalizer  (Aditi)
        ├── services/ai_analysis/       LLM providers, prompts, engine   (Samay)
        ├── services/risk_analysis/     scoring, control mapping, gaps   (Samay)
        ├── services/policy_generation/ templates, generator, quality    (Suraj)
        ├── services/document_generation/ document model, DOCX, PDF      (Suraj)
        └── database/ + models/     SQLAlchemy (SQLite by default)
                │
                ▼
   backend/data/   uploads/  extracted/  generated_reports/  cache/  policygen.db
```

Everything long-running (extraction, analysis, generation, revision) runs as a
persisted **job** on a thread pool. HTTP requests return immediately with a job
id; the UI polls `GET /api/jobs/{id}` and shows the stage the server reports.

## 2. Team modules and how they communicate

| Module | Owner | Code | Consumes | Produces |
|---|---|---|---|---|
| 1. Input file processing | Aditi | `services/file_processing/` | Uploaded bytes | `NormalizedDocument` (JSON on disk) + `ExtractedDocument` row |
| 2. AI analysis logic | Samay | `services/ai_analysis/`, `services/risk_analysis/`, `prompts/analysis_*.md` | `NormalizedDocument`s | `Analysis` + `AnalysisFinding` rows (validated) |
| 3. Policy generation | Suraj | `services/policy_generation/`, `services/document_generation/`, `templates/`, `prompts/policy_*.md` | Validated analysis + `PolicyConfig` + template | `Policy` + `PolicyVersion` rows, DOCX and PDF files |
| 4. Integration and testing | Mayuri | `api/`, `services/jobs.py`, `core/`, `frontend/`, `backend/tests/`, `scripts/` | All of the above | REST API, web app, job orchestration, test suites |

The modules only talk through three data contracts, so each can be developed
and tested on its own:

1. **`NormalizedDocument`** (`schemas/documents.py`) – Module 1 → Module 2
2. **Analysis findings** (`schemas/analysis.py`, stored in `analysis_findings`) – Module 2 → Module 3
3. **Policy content / document model** (`schemas/policy.py`, `document_generation/model.py`) – Module 3 → renderers and UI

## 3. Data flow

```
File Upload → Validation → Extraction → Structuring → AI Analysis → Requirements
→ Risk and Control Mapping → Policy Generation → Formatting → Preview → Download
```

### 3.1 Upload and validation (`file_processing/service.py`, `validation.py`)

1. The file name is sanitized for display; it never becomes a storage path.
2. Extension must be supported; the declared MIME type must be plausible.
3. The upload is streamed to a temp file while its size is counted and a
   SHA-256 is computed. Exceeding `MAX_UPLOAD_MB` aborts and deletes it.
4. The **signature** is checked on the stored bytes (PDF header, PNG/JPEG/TIFF
   magic, OLE magic for legacy Office, ZIP + required part for OOXML, no binary
   data in text formats). OOXML archives are checked for decompression bombs.
5. Same hash as an existing file → the existing record is returned (`duplicate: true`).
6. The file is moved to `data/uploads/<id>.<ext>` and an extraction job is queued.

### 3.2 Extraction and normalization

One parser per format, registered with `@register_parser` (`file_processing/base.py`):

| Extension | Parser | Library | Notes |
|---|---|---|---|
| txt, md | `parsers/text.py` | – | Heading detection; Markdown tables |
| pdf | `parsers/pdf.py` | pypdf, pdfplumber, pypdfium2 | Tables extracted separately, wrapped lines re-joined, OCR for scans |
| docx | `parsers/docx_parser.py` | python-docx | Heading styles, lists, tables in reading order |
| doc | `parsers/doc_legacy.py` | olefile | Piece-table text extraction, no styles |
| xlsx / xls | `parsers/excel.py` | openpyxl / xlrd | One table per worksheet |
| csv | `parsers/csv_parser.py` | csv | Delimiter sniffing, BOM handling |
| json | `parsers/json_parser.py` | json | Record lists become tables |
| xml | `parsers/xml_parser.py` | defusedxml | Entities and DTDs blocked |
| html, htm | `parsers/html_parser.py` | BeautifulSoup | Scripts and styles discarded |
| pptx | `parsers/pptx_parser.py` | python-pptx | One section per slide, tables, notes |
| ppt | `parsers/ppt_legacy.py` | olefile | Text atoms per slide |
| png, jpg, jpeg, tif, tiff | `parsers/image.py` | Pillow + OCR engine | Tesseract, or Claude vision when opted in |

Parsers run on a watchdog thread with `EXTRACTION_TIMEOUT_SECONDS` and hard
limits (pages, rows, characters). Parser failures become `ParserError(code, message)`
with one of: `empty`, `corrupted`, `encrypted`, `unsupported`, `ocr_unavailable`,
`too_large`, `timeout`.

The normalizer produces:

```json
{
  "document_id": "…", "file_id": "…", "file_name": "security_assessment.xlsx", "file_type": "xlsx",
  "document_title": "Security Assessment",
  "extracted_text": "…",
  "sections": [{"section_id": "p2-s1", "heading": "3. Access Control", "level": 1, "text": "…", "location": "page 2 > 3. Access Control"}],
  "tables":   [{"table_id": "t1", "title": "Risk Register", "location": "sheet 'Risk Register'", "headers": ["…"], "rows": [["…"]], "truncated": false}],
  "metadata": {"page_count": null, "sheet_names": ["Risk Register"], "upload_timestamp": "…", "extraction_status": "completed", "warnings": []}
}
```

Every section and table carries a `location`; that is what makes later findings traceable.

### 3.3 AI analysis (`ai_analysis/engine.py`)

```
documents ─► chunk_document()            sections/tables grouped up to CHUNK_MAX_CHARS,
                                         oversized ones split; headings + locations kept
          ─► per chunk: extract_findings  LLM call → ChunkFindings (validated, repaired once)
          ─► merge_findings()             permanent ids REQ-/RSK-/CTL-, de-duplication,
                                         evidence quotes verified against the source text
          ─► consolidate                  LLM call over the merged findings → summary,
                                         recommended policy, gap analysis, contradictions,
                                         missing information, assumptions, recommendations
          ─► post-processing (code)       risk scores and levels, dangling references removed,
                                         a gap entry for every requirement
```

* **Context management.** Each document is analyzed on its own; consolidation
  sees the merged findings, never the raw text, so multi-file analyses stay
  inside the model's context. `MAX_CHUNKS_PER_ANALYSIS` bounds cost.
* **Partial failure.** A failed chunk is recorded in `processing.failed_chunks`
  and the rest continue. If every chunk fails, the job fails.
* **Retry without re-paying.** Chunk results are cached in `data/cache/` until
  the job succeeds; a retry after a consolidation failure repeats only that call.
* **Validation.** `structured.structured_call()` parses the response, validates
  it with Pydantic, and on failure sends one repair request
  (`LLM_JSON_REPAIR_ATTEMPTS`). Invalid output never reaches Module 3.

#### Analysis schema (as returned by `GET /api/analyses/{id}`)

| Key | Content |
|---|---|
| `summary` | `overall_summary`, `document_summaries[]`, `document_category`, `subject_areas[]`, `organization_name`, `stakeholders[]`, `systems[]` |
| `requirements[]` | `ref_id`, `statement`, `category` (+`category_label`), `basis` (documented/inferred), `confidence`, `source` |
| `risks[]` | `ref_id`, `title`, `description`, `category`, `origin` (documented/inferred), `likelihood`, `impact`, `score`, `level`, `scoring_basis` (documented/estimated/user), `existing_controls[]`, `recommended_mitigation`, `residual_*`, `confidence`, `source` |
| `controls[]` | `ref_id`, `name`, `objective`, `description`, `control_type` (administrative/technical/physical), `control_function` (preventive/detective/corrective), `status`, `basis` (existing/recommended), `owner`, `related_requirements[]`, `related_risks[]`, `recommended_action`, `source` |
| `gaps[]` | `ref_id`, `requirement_id`, `requirement`, `existing_evidence`, `status` (compliant/partially_addressed/not_evidenced/not_applicable), `identified_gap`, `associated_risk_ids[]`, `recommended_control_ids[]`, `priority`, `suggested_action` |
| `responsibilities[]` | `role`, `responsibility`, `basis` (documented/proposed), `source` |
| `recommendations[]` | `title`, `description`, `priority`, `related_ids[]` |
| `contradictions[]`, `missing_information[]`, `assumptions[]` | Cross-document findings |
| `source_references[]` | Flat list: finding → file, location, evidence, `evidence_verified` |
| `security_flags[]` | Instruction-like text found in uploads (prompt-injection attempts) |
| `processing` | `chunks_total`, `chunks_failed`, `failed_chunks[]`, `warnings[]` |
| `risk_summary`, `gap_summary`, `control_summary`, `control_matrix` | Statistics over the *active* (non-excluded) findings |

Every `source` is `{file_name, location, evidence, evidence_verified}`. Each
finding also carries `user_edited`, `excluded` and (when edited) `original`.

#### Risk scoring (`risk_analysis/scoring.py`)

`score = likelihood × impact`, both 1–5. Levels with the default
`RISK_LEVEL_THRESHOLDS=4,9,15`: Low 1–4, Medium 5–9, High 10–15, Critical 16–25.
A 0 means "not assessable" and yields the level `unrated`. The LLM supplies
likelihood and impact; scores and levels are always computed in code.

### 3.4 Policy generation (`policy_generation/generator.py`)

```
analysis (active findings) + PolicyTemplate + PolicyConfig
   ─► generate_policy   LLM call → PolicyContent {title, sections[{id, heading, blocks[]}]}
   ─► normalise         template section order and headings, empty blocks dropped
   ─► finalise          unknown finding ids stripped, statements numbered (e.g. ACP-007)
   ─► quality.validate_policy()
        └─ if correctable issues: ONE revise_sections call (QUALITY_CORRECTION_ROUNDS),
           kept only if it does not increase the number of errors
   ─► PolicyVersion 1 saved                                  stage: generating_policy
   ─► render DOCX / PDF from the document model              stage: formatting_document
   ─► completed
```

A block is one of `paragraph`, `subheading`, `bullets`, `numbered`, `statements`
(each statement has `id`, `text`, `basis`, `refs[]`) or `table`.

**Templates** are JSON objects in `backend/app/templates/policy_types/` (or in
`POLICY_TEMPLATES_DIR`). Each defines id, name, code, purpose, keywords,
requirement categories, excluded sections, topic groupings, objectives,
principles, definitions and monitoring points. The shared section library is
`templates/sections.json`. Adding a JSON file adds a policy type everywhere.

**Structure rules** (`structure.py`, `abbreviations.py`, `people.py`). The
section order lives only in `templates/sections.json`; prompts, quality
checks, renderers and the web app read it from there. `structure.enforce`
runs inside `_finalise`, which every path that creates a version goes through
(draft, quality correction, AI revision, regeneration, manual edit), on a copy
of the content so that stored versions are never altered. It:

* drops retired sections (`"retired": true` in `sections.json`, currently the
  Executive Summary), matched by id or by heading, so neither a model's output
  nor a policy saved by an earlier release can carry one into a new version;
* puts the sections in template order, which is what places Abbreviations
  immediately before Scope and drives the heading numbers;
* replaces personal names by the role they hold. A name counts as a person's
  only on evidence (honorific, common given name, a role title beside it, or
  the analysis or configuration naming the person); the role comes from the
  same evidence or becomes a placeholder; a doubtful label is reported, not
  rewritten. References and the appendices are left alone;
* rebuilds the Abbreviations section from the abbreviations the other
  sections use (the model's rows, expansions spelled out in the text, and
  `templates/abbreviations.json`), and removes from Definitions entries that
  only expand an abbreviation.

What it changed is added to the quality report as `info` notes.

**Quality checks** (`quality.py`): missing mandatory sections, empty sections,
repeated sections or paragraphs, standards/laws the sources never mention,
assertions of certification or compliance, references to non-existent finding
ids, duplicate statement ids, documented statements without a source, a
requirements section with no statements, unclear responsibilities,
contradictory values between sections, invalid table structure, a retired
section, Abbreviations not immediately before Scope or not a two-column table,
entries repeated between Definitions and Abbreviations, personal names in role
references (`personal_name`, eligible for the correction round), and a list of
unresolved placeholders.

**Document model** (`document_generation/model.py`): one neutral structure –
meta, notices, document control, version history, numbered sections, and the
appendices built in code from the findings (risk register with risk matrix,
control matrix, gap analysis, evidence mapping, outstanding information). The
DOCX renderer, the PDF renderer and the web preview all consume this same
model, which is what keeps the three consistent.

**Versions.** Editing a section, regenerating sections or an AI revision each
create a new `PolicyVersion`; nothing is overwritten. A review decision applies
to the version it was made for.

## 4. Prompt architecture

| File | Used for |
|---|---|
| `prompts/analysis_system.md` | Role, treatment of uploads as untrusted data, evidence rules, output contract |
| `prompts/analysis_extract_user.md` | Per-chunk extraction task, categories, scoring method, schema, fenced document |
| `prompts/analysis_consolidate_user.md` | Cross-document task, policy types, gap statuses, schema, fenced findings |
| `prompts/policy_system.md` | Policy-writer role, no unsupported claims, placeholder form, documented vs proposed |
| `prompts/policy_generate_user.md` | Configuration, style, section list with guidance, block types, schema, fenced analysis |
| `prompts/policy_revise_user.md` | Section revision for corrections, regeneration and AI-assisted edits |
| `prompts/json_repair_user.md` | Bounded repair of an invalid response |

Prompts are rendered by `prompts.render()` in a single pass, so `{{…}}` inside
a document cannot pull other values in.

## 5. LLM providers

`providers/base.py` defines `LLMProvider.complete(LLMRequest) -> LLMResponse`.
`LLMRequest` carries the task name, the rendered system and user prompts, the
JSON schema, and the structured payload the prompt was built from.

* **`AnthropicProvider`** – official `anthropic` SDK, model `claude-opus-5-5`
  (configurable), streaming, adaptive thinking, `output_config.effort`,
  structured outputs (`output_config.format` with the task's JSON schema) and
  the server-side refusal fallback. Both optional features are dropped
  automatically, once, if the API rejects them. SDK errors are mapped to
  `LLMTimeoutError`, `LLMRateLimitError`, `LLMUnavailableError`,
  `LLMConfigurationError`, `LLMRefusalError` with user-safe messages.
* **`GeminiProvider`** – official `google-genai` SDK, model `gemini-3.8-flash`
  (configurable with `GEMINI_MODEL`). Sends the system prompt as
  `system_instruction`, asks for `application/json` constrained to the task's
  schema with `response_json_schema`, and sets the timeout and bounded retries
  from the shared `LLM_*` settings. A rejected schema or output-token limit is
  dropped automatically, once. Invalid key (400/401/403), unsupported model
  (404), quota (429), overload (5xx), timeouts and network failures are mapped
  to the same `LLM*Error` types, and safety blocks to `LLMRefusalError`. The
  provider's reason is written to the server log with the key redacted.
* **`OpenRouterProvider`** – plain `httpx` POST to
  `https://openrouter.ai/api/v1/chat/completions` with Bearer authentication;
  model from `OPENROUTER_MODEL` (default `openrouter/free`). The system and
  user prompts become chat messages and the task's schema is sent as a strict
  `json_schema` response format. Because models on OpenRouter differ, the
  format steps down to `json_object` and then to none when the API reports
  the model cannot do it, and an output limit the model rejects is dropped.
  Schemas are sent with `$ref`s expanded, because hosts behind OpenRouter
  differ in whether they resolve them. Timeouts, per-minute 429 and 5xx are
  retried a bounded number of times (honouring `Retry-After` and
  `X-RateLimit-Reset`, at most a minute per wait). 401, 402, 403, 404, the
  daily free-model limit and errors delivered with HTTP 200 are mapped to
  the shared `LLM*Error` types and are not retried. `llm_quota_exceeded`
  (no credit, daily limit) also stops an analysis at the first segment that
  hits it, since every further request would fail and count against the
  account; finished segments stay cached for the retry. Logged details have
  the key redacted.

  The answer is read as a server-sent event stream under one total time
  limit (`OPENROUTER_TIMEOUT_SECONDS`), checked on every line received.
  OpenRouter sends keep-alive bytes every few seconds while a model works,
  so a per-read timeout never fires for a model that does not finish; the
  total limit, plus a 90-second stall limit once the answer has started,
  is what guarantees that a call, and so a job, always ends. Either limit
  is retried once. `reasoning` is sent as disabled unless configured
  otherwise, and documents are split into `OPENROUTER_CHUNK_MAX_CHARS`
  parts (`LLMProvider.chunk_max_chars`), because free models write slowly
  and the findings are several times longer than their source. While it
  waits, the provider calls `LLMRequest.observer` with the model, the phase
  (waiting, reasoning, writing) and the elapsed time. The pipeline sets the
  observer to `progress_reporter(ctx.update, ...)`, which stores the job
  message the UI polls and raises `JobCancelled` when the user cancelled,
  so a request in flight is abandoned within seconds.
* **`MockProvider`** – rule-based stand-in used when no key is configured.
  It implements the same four tasks with keyword rules and sentence templates
  and returns JSON that goes through the same validation. `simulated=True`
  follows the analysis and the policy into the UI and the documents.

Selection (`providers/factory.py`): `LLM_PROVIDER=auto` picks Claude when
`ANTHROPIC_API_KEY` is set, else Gemini when `GEMINI_API_KEY` is set, else
OpenRouter when `OPENROUTER_API_KEY` is set, else the mock; `anthropic`,
`gemini`, `openrouter` and `mock` force one, and a forced provider that is
not configured reports that instead of falling back. The factory reads its
settings through `get_llm_settings()`, which re-reads `backend/.env` when the
file has changed since startup (an invalid edit keeps the last good values),
so the provider shown and used always matches the file; all other settings
are read once at startup. Each provider declares a
`display_name` and a `service_name` ("Anthropic API", "Google Gemini API",
"OpenRouter API"). The health endpoint returns them with the configured model,
and `/api/config` returns the id-to-name map (`llm_providers`), so the UI shows
the provider and model actually in use, and names where documents are sent,
without any provider names written into the frontend.

To add a vendor: subclass `LLMProvider` and add it to `_BUILDERS` in `providers/factory.py`.

## 6. Processing state machine (`core/stages.py`)

```
uploaded → validating → extracting → extracted → analyzing → analysis_completed
        → generating_policy → formatting_document → completed
any stage → failed | cancelled        failed/cancelled → (retry) → the stage it restarts at
```

`advance()` rejects any other transition; `failed → completed` is impossible.
Jobs additionally have a status: `queued`, `running`, `completed`, `failed`,
`cancelled`. A job is only marked completed after its handler returned
normally. On start-up `recover_interrupted_jobs()` marks queued/running jobs
(and their file, analysis or policy) as failed so they can be retried.

## 7. API

Base path `/api`. Errors are always `{"error": {"code", "message", "details"?}}`.
Interactive documentation: `http://localhost:8000/api/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Database, LLM provider and mode, OCR, privacy notice |
| GET | `/config` | Limits, file types, policy types, styles, labels, scoring method |
| GET | `/dashboard` | Statistics, recent reports, analyses and jobs |
| GET | `/history` | Recent background jobs |
| POST | `/upload` | Upload one or more files (multipart field `files`); per-file result |
| GET | `/files` | List files (`?ids=a,b` to poll specific ones) |
| GET | `/files/{file_id}` | File status and extraction summary |
| GET | `/files/{file_id}/content` | Extracted sections and tables (preview) |
| POST | `/extract/{file_id}` | Run or re-run extraction |
| DELETE | `/files/{file_id}` | Delete stored bytes, extracted content and record |
| POST | `/analyses` | Analyze files together: `{"file_ids": [...]}` → 202 + job |
| POST | `/analyze/{file_id}` | Analyze a single file |
| GET | `/analyses`, `/analyses/{id}` | List / read (full findings once completed) |
| PATCH | `/analyses/{id}/findings/{finding_id}` | `{data?, excluded?, restore?}` |
| DELETE | `/analyses/{id}` | Delete analysis, findings and derived reports |
| GET | `/policies/types` | Available policy types and their sections |
| POST | `/policies/recommend` | `{"analysis_id"}` → ranked types with reasons |
| POST | `/policies/generate` | `{"analysis_id", "config": PolicyConfig}` → 202 + job |
| GET | `/jobs/{job_id}` | Status, stage, progress, message, error |
| POST | `/jobs/{job_id}/cancel`, `/jobs/{job_id}/retry` | Cancel / retry |
| GET | `/reports` | Report history |
| GET | `/reports/{id}` | Report with document model (`?version=n`) |
| GET | `/reports/{id}/versions`, `/reports/{id}/compare?from=1&to=2` | Versions and diff |
| PUT | `/reports/{id}/sections/{section_id}` | Save a user edit as a new version |
| POST | `/reports/{id}/sections/regenerate` | `{"section_ids", "instruction"?}` → job |
| POST | `/reports/{id}/review` | `{"action": submit\|approve\|request_changes\|reopen, "actor", "comment"}` |
| GET | `/reports/{id}/download?format=docx\|pdf&version=n` | Download |
| DELETE | `/reports/{id}` | Delete report, versions and rendered files |

## 8. Database

SQLAlchemy models in `models/entities.py`:

| Table | Holds |
|---|---|
| `uploaded_files` | Name, type, size, hash, storage name, status, error |
| `extracted_documents` | Title, counts, metadata, pointer to the content JSON |
| `jobs` | Type, status, stage, progress, message, error, payload, result, dedupe key |
| `analyses` | Status, source files, provider/model, `simulated`, summary JSON |
| `analysis_findings` | Kind, ref id, data, `original_data`, `user_edited`, `excluded` |
| `policies` | Type, title, config, status, review status, current/approved version |
| `policy_versions` | Version number, content JSON, quality report, origin, change note |
| `review_events` | Review actions with actor, comment and version |
| `processing_errors` | Stage, code and message of every failed job |

Uploads, extracted content and rendered documents are files in `DATA_DIR`,
not database blobs. Only portable column types are used, so `DATABASE_URL` can
point at PostgreSQL without code changes.

## 9. Security design

| Threat | Control |
|---|---|
| Malicious or mislabelled upload | Extension + MIME + signature checks; size and decompression limits; no macros or scripts executed; XML via defusedxml; timeouts |
| Path traversal | Storage names are generated ids; `storage._resolve` refuses anything outside its directory |
| Prompt injection in documents | System-prompt rules; content fenced in tags with a random id; closing tags inside content neutralized; injection scanner flags; schema validation; evidence verification; reference validation |
| Fabricated findings | Evidence quotes checked against the extracted text and flagged `evidence_verified: false` |
| Unsupported claims in policies | Quality check against standards the sources mention; certification/compliance assertions flagged |
| Secret exposure | Keys (Anthropic, Gemini, OpenRouter) only in server environment; never sent to the frontend; error messages are generic; logs contain no document content |
| Abuse | Sliding-window rate limits (stricter for expensive endpoints); optional bearer token; explicit CORS origins |
| Data lifetime | Delete endpoints remove stored files; optional `RETENTION_HOURS`; `DELETE_SOURCE_AFTER_EXTRACTION` |

The application is single-workspace: there are no user accounts, so everyone
who can reach it (and knows the optional token) sees the same files and reports.
