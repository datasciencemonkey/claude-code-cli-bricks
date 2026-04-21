---
description: "Report prompt-cache hit rate + token savings for recent Claude Code sessions (reads MLflow traces)"
---

Analyse prompt-cache performance for this user's recent Claude Code sessions
in CODA. Traces are captured by `setup_mlflow.py` when MLflow tracing is
enabled; they include per-request token usage from Anthropic, which is
what reveals caching.

## Steps

1. **Check tracing is on.**
   Read `os.environ.get("MLFLOW_CLAUDE_TRACING_ENABLED", "")`. If it's empty,
   `"0"`, or `"false"` (case-insensitive), tell the user tracing is off and
   stop — suggest they re-run setup with `MLFLOW_CLAUDE_TRACING_ENABLED=true`
   or flip it in `app.yaml`.

2. **Resolve the experiment path.**
   The setup logs to `/Users/{email}/{app_name}` where:
   - `email` = `APP_OWNER_EMAIL` env var, or `databricks current-user me`
   - `app_name` = `DATABRICKS_APP_NAME` env var, or the basename of `$HOME`

3. **Query recent traces.** Use `mlflow` (already installed in CODA) to list
   the last ~50 traces in that experiment. Anthropic / Claude Code traces
   carry per-call token usage on the root span outputs (or `info.tags`):
   - `input_tokens` — uncached input
   - `cache_read_input_tokens` — served from cache
   - `cache_creation_input_tokens` — written to cache
   - `output_tokens`

   Sum each across all traces.

4. **Report a compact summary.** Include:
   - **Hit rate** = `cache_read / (cache_read + input_tokens)`, as a %
   - **Cached tokens served** (with the cost context that cache-read ≈ 10%
     of base input price)
   - **Totals**: input / cache_read / cache_creation / output
   - **Estimated $ saved vs uncached** — assume Claude Opus pricing unless
     `ANTHROPIC_MODEL` env var says otherwise:
     `saved ≈ cache_read_tokens × (input_price − cache_read_price) / 1e6`
     (Opus: input $15/MTok, cache_read $1.50/MTok → $13.50 saved per M
     cache_read tokens.)

5. **If hit rate < 50%, diagnose.** Likely causes in order:
   - Prefix < 1024 tokens (Databricks passthrough minimum — won't cache)
   - Sessions spaced > 5 min apart (ephemeral TTL expired)
   - System prompt changed between calls (non-deterministic skill loading,
     varying `CLAUDE.md` content, or model/route switch)
   - Tracing only captures a subset of calls (check `MLFLOW_TRACE_SAMPLING`)

Keep the output tight — 10-15 lines, not a report. This is observability,
not a presentation.
