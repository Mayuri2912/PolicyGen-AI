# Analysis performance

New analyses use a 10,000-character findings budget for bounded consolidation
instead of 2,500. Oversized or truncated batches still split recursively, retaining
all findings and checkpointing successful children. Existing checkpoints retain
completed batches and split trees, but combine untouched batches into the larger
budget. The exact upgraded plan is saved before calling a provider, preserving
cache keys across subsequent retries and configuration changes.
Batch counts appear in progress messages. Partial batches still carry the existing
warning that cross-batch coverage and contradictions need reviewer confirmation.

`CONSOLIDATION_BATCH_CHARS` adjusts the findings budget (clamped to 2,500–12,000).
It is not a total prompt or token limit: schema, catalogue and supporting controls
add overhead. Provider rejection triggers the existing adaptive splitting.

`ANALYSIS_CALL_TIMEOUT_SECONDS` defaults to 180 (minimum 30). Analysis requests
pass this deadline to the OpenRouter-compatible streaming transport used by
OpenRouter, Groq and NVIDIA. Other providers retain their own SDK timeouts.
Generation keeps its existing timeout. A timed-out credential is skipped for at
least five minutes before regaining its original priority. Quota reset handling
and free-model selection are unchanged.

Regression tests cover fewer consolidation calls with all requirement IDs retained,
resume without repeated calls, old checkpoint compatibility, adaptive splitting,
request deadlines and slow-provider cooldown. These are controlled transport tests;
actual elapsed time still depends on provider capacity and document complexity.
