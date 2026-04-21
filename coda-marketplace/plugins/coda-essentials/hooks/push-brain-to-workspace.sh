#!/usr/bin/env bash
# Stop hook: push Claude Code's auto-memory to Databricks Workspace so it
# survives app redeployment. Fire-and-forget: runs in background, never blocks.

set -euo pipefail

APP_DIR="/app/python/source_code"
SYNC_SCRIPT="$APP_DIR/claude_brain_sync.py"
LOG="$HOME/.brain-sync.log"

[ -f "$SYNC_SCRIPT" ] || exit 0

nohup uv run --project "$APP_DIR" python "$SYNC_SCRIPT" push \
  >> "$LOG" 2>&1 & disown
