#!/usr/bin/env bash
# Stop hook: if the session did meaningful work, suggest /til to crystallize it.
# "Meaningful" = >=1 recent commit OR 3+ changed files.

set -euo pipefail

MIN_COMMITS=1
MIN_CHANGED_FILES=3
SINCE="2 hours ago"

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

author=$(git config user.name 2>/dev/null || echo "")
[ -n "$author" ] || exit 0

commit_count=$(git log --author="$author" --since="$SINCE" --oneline 2>/dev/null | wc -l | tr -d ' ')
changed_files=$(git diff --name-only HEAD 2>/dev/null | wc -l | tr -d ' ')
staged_files=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
total_changed=$((changed_files + staged_files))

if [ "$commit_count" -ge "$MIN_COMMITS" ] || [ "$total_changed" -ge "$MIN_CHANGED_FILES" ]; then
  summary=""
  if [ "$commit_count" -gt 0 ]; then
    summary="${commit_count} commit(s) this session"
  fi
  if [ "$total_changed" -gt 0 ]; then
    if [ -n "$summary" ]; then
      summary="$summary, ${total_changed} uncommitted file(s)"
    else
      summary="${total_changed} uncommitted changed file(s)"
    fi
  fi

  cat <<EOF
{"systemMessage": "Session had meaningful work (${summary}). Consider running /til to capture what you learned before closing."}
EOF
fi
