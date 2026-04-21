#!/usr/bin/env bash
# PostToolUse (Edit|Write) hook: stamp `last_verified: YYYY-MM-DD` on memory
# files after they are edited. Operates only on Claude Code auto-memory files
# under ~/.claude/projects/*/memory/*.md (excluding the MEMORY.md index).
#
# Uses GNU sed syntax (Linux). Do not use BSD sed forms (`sed -i ''`) here.

set -euo pipefail

filepath="${CLAUDE_FILE_PATH:-}"

[[ -n "$filepath" ]] || exit 0
[[ "$filepath" == *"/.claude/projects/"*"/memory/"* ]] || exit 0
[[ "$filepath" == *.md ]] || exit 0
[[ "$(basename "$filepath")" != "MEMORY.md" ]] || exit 0
[[ -f "$filepath" ]] || exit 0

today=$(date +%Y-%m-%d)

head -1 "$filepath" | grep -q '^---' || exit 0

if grep -q '^last_verified:' "$filepath"; then
  sed -i "s/^last_verified:.*$/last_verified: $today/" "$filepath"
else
  awk -v stamp="last_verified: $today" '
    /^---$/ { count++ }
    count == 2 && inserted == 0 { print stamp; inserted = 1 }
    { print }
  ' "$filepath" > "${filepath}.tmp" && mv "${filepath}.tmp" "$filepath"
fi
