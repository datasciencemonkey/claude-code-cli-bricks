#!/usr/bin/env bash
# Stop hook: flush the Claude Code session transcript to an MLflow trace.
#
# Claude Code pipes the hook-event JSON to our stdin. We capture that
# synchronously (fast, bounded to one read) then background the actual
# flush with stdin redirected from the captured file. This way:
#
#   - the wrapper returns in <1s, unblocking the Stop chain
#     (crystallize-nudge, brain-push, /til)
#   - a hard `timeout 30` caps the backgrounded handler so a stall in
#     transcript processing can't hold memory/CPU indefinitely
#   - stop_hook_handler() actually receives its hook-event JSON, which
#     naive `nohup ... & disown` would have redirected to /dev/null

set -euo pipefail

APP_DIR="/app/python/source_code"
LOG="$HOME/.mlflow-hook.log"
STDIN_FILE="$(mktemp -t mlflow-hook.XXXXXX)"

# Synchronous: read Claude Code's hook-event JSON from stdin.
cat > "$STDIN_FILE"

# Async: run the handler in the background with the captured stdin.
# The subshell cleans up the temp file after timeout/handler exits.
nohup bash -c "
  timeout 30 uv run --project '$APP_DIR' python -c \
    'from mlflow.claude_code.hooks import stop_hook_handler; stop_hook_handler()' \
    < '$STDIN_FILE'
  rm -f '$STDIN_FILE'
" >> "$LOG" 2>&1 & disown
