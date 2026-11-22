# Workspace release

Existing records remain in the default workspace. The additive SQL migration must run in the private policygen schema before upgrading a restricted PostgreSQL installation. SQLite is migrated automatically on startup. No existing content is deleted.

## First administrator
Open Account & administration with the existing access token, or choose first-time setup on the sign-in page. Enter the existing setup token and your own name, email and password. After setup, shared-token data access is disabled. Sign in using your individual account. Keep at least one active administrator. Administrators create accounts; editors generate and edit; reviewers approve; viewers read. Isolated workspace accounts cannot access default-workspace records.

## Review and reliability
Readiness checks identify missing information, structural problems and manual review items. They do not certify ISO 27001 compliance. Approval requires an authenticated administrator or reviewer, the current version, acknowledged checks and a rationale where warnings remain.

Generation saves validated section batches. Retrying the same configuration resumes saved batches. A changed configuration starts a fresh checkpoint. Free provider quotas still apply. The static frontend remains available while the free backend starts; requests and polling use bounded timeouts and backoff.

Automatic model retries are bounded to two extra attempts with 5/10-second delays by default. LLM_FALLBACK_PROVIDERS=openrouter enables the configured OpenRouter credentials after Gemini failures. Successful batches stay saved. Cancellation stops retries; refusals and bad requests do not trigger fallback. There is no simulated fallback. Provider SDK retries and Retry-After delays can extend the total wait.

Groq is supported as a primary provider (LLM_PROVIDER=groq) or a fallback (LLM_FALLBACK_PROVIDERS=openrouter,groq). GROQ_API_KEY and optional GROQ_API_KEYS stay server-side. The default model is openai/gpt-oss-120b; JSON mode is validated and repaired using the same policy schemas.

Large consolidation inputs are divided into bounded batches when Groq is configured. All extracted findings remain stored; successful consolidation batches are cached for retry. Explicitly linked controls accompany requirement batches where they fit. Cross-batch coverage and contradictions require reviewer confirmation, and this limitation is included in analysis assumptions. Oversized Groq requests fail with an actionable size-limit error and can fall back to another configured provider without repeatedly sending an impossible request.

Consolidation adapts to output truncation by dividing failed batches into smaller children, with a bounded split depth. Split plans and successful child results are cached, so retrying does not repeat known oversized parent calls. Groq GPT-OSS uses low reasoning effort and reserves more answer tokens. The policy catalogue retains all IDs with less repeated prompt text; provider-specific error messages identify Groq correctly.

## Free-tier quota waits and key switching
With multiple configured providers/keys, vendor SDK retries are disabled: a failed key yields immediately to the next eligible key. Invalid credentials are disabled until configuration is reloaded; rate limits use reported reset delays; daily quota errors without a reset estimate use a conservative 24-hour cooldown. Retry loops never bypass cooldowns. Request-size/output failures do not disable credentials, allowing smaller requests to proceed.

When all providers are quota-limited, threaded jobs retain their checkpoints, show a next eligibility-check time and automatically queue a retry. This is bounded to three quota deferrals per job; manual retry starts a fresh deferral allowance. Cancellation is immediate during a quota wait. Wait metadata survives a backend restart. On free Render, scheduled checks depend on the backend running; startup restores pending checks when the service wakes. Account & administration shows recent provider/key eligibility, not an exact remaining-token balance.

Additional comma-separated server-side keys are supported by GEMINI_API_KEYS, OPENROUTER_API_KEYS, GROQ_API_KEYS and ANTHROPIC_API_KEYS. More keys do not increase shared project/account/organization quotas. No paid services are enabled by this release.

PUBLIC_ACCESS=true removes authentication for the shared default workspace. Anyone with the site URL can read, generate, edit, review and delete its documents. Existing isolated customer workspaces are not selected by public requests, even when a previous account token is supplied. Account administration endpoints are blocked. Public reviews are recorded as Public workspace reviewer, without claiming an authenticated personal identity. Set PUBLIC_ACCESS=false to restore account-based access.

OPENROUTER_API_KEY2 is supported as a second credential, after OPENROUTER_API_KEY and before the comma-separated pool. With LLM_PROVIDER=openrouter and LLM_FALLBACK_PROVIDERS=gemini,groq, eligible OpenRouter keys are tried before Gemini and Groq. Provider priority is checked on every request; cooled-down keys remain skipped until eligible again.

Waiting jobs store a non-reversible provider-configuration fingerprint. After a restart with changed keys, models or provider priority, their saved work is scheduled immediately rather than waiting for obsolete quota timers. No credential values are stored in job records.

NVIDIA NIM is available as LLM_PROVIDER=nvidia or a fallback via LLM_FALLBACK_PROVIDERS=gemini,groq,nvidia. NVIDIA_API_KEY and optional NVIDIA_API_KEYS are server-side. The default model is nvidia/nemotron-3.5-lightning-30b-a3b, with a 4096-token answer cap. JSON is requested in the prompt, then validated and repaired by the same task schemas. Keys, model and priority changes resume quota-waiting jobs.

Gemini defaults to gemini-3.1-flash-lite, verified with the configured key. The older, cheaper gemini-2.5-flash-lite was rejected for this account. Flash-Lite supports free-tier text generation; a lower price does not increase project quotas automatically. Keep existing schema validation and review requirements for all generated policies.

GEMINI_API_KEY2 is supported after GEMINI_API_KEY and before GEMINI_API_KEYS. Both Gemini keys are attempted before Groq and NVIDIA, after both OpenRouter keys. Duplicate credentials are removed. Keys from the same Google project share quotas; a second key alone does not increase that project's request limits.

OPENROUTER_API_KEY3 is supported after OPENROUTER_API_KEY2 and before OPENROUTER_API_KEYS. All three numbered OpenRouter keys precede Gemini, Groq and NVIDIA. Duplicate credentials are removed and exhausted keys remain subject to cooldowns.

Policy generation saves valid sections from partial batches immediately. Missing, empty or duplicated sections receive up to two automatic single-section recovery requests. Retry requests only unfinished sections; persistent omissions remain blocked from publication with checkpoints retained.
