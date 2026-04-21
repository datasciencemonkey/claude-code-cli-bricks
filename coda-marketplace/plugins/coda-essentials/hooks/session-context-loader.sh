#!/usr/bin/env bash
# SessionStart hook: inject recent git activity into context so Claude
# knows what was happening last session.

set -euo pipefail

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

branch=$(git branch --show-current 2>/dev/null || echo "detached")
repo_name=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || basename "$PWD")

recent_commits=$(git log --all --since="7 days ago" \
  --format="%h %ad %an: %s" --date=relative --max-count=10 2>/dev/null || true)

author=$(git config user.name 2>/dev/null || echo "")
last_own_commit=""
if [ -n "$author" ]; then
  last_own_commit=$(git log --author="$author" --format="%ad" \
    --date=relative --max-count=1 2>/dev/null || true)
fi

status_summary=$(git status --short 2>/dev/null | head -15 || true)
status_count=$(git status --short 2>/dev/null | wc -l | tr -d ' ')

active_branches=$(git for-each-ref --sort=-committerdate \
  --format='%(refname:short) (%(committerdate:relative))' \
  refs/heads/ --count=5 2>/dev/null || true)

open_prs=""
if command -v gh >/dev/null 2>&1; then
  open_prs=$(gh pr list --author="@me" --state=open \
    --json number,title,headRefName \
    --jq '.[] | "#\(.number) [\(.headRefName)] \(.title)"' \
    2>/dev/null | head -5 || true)
fi

ctx="Session context for ${repo_name} (branch: ${branch})"
[ -n "$last_own_commit" ] && ctx="${ctx}
Your last commit: ${last_own_commit}"
[ -n "$recent_commits" ] && ctx="${ctx}

Recent commits (7d):
${recent_commits}"
if [ -n "$status_summary" ]; then
  if [ "$status_count" -gt 15 ]; then
    ctx="${ctx}

Uncommitted changes (${status_count} files, showing first 15):
${status_summary}"
  else
    ctx="${ctx}

Uncommitted changes:
${status_summary}"
  fi
fi
[ -n "$active_branches" ] && ctx="${ctx}

Active branches:
${active_branches}"
[ -n "$open_prs" ] && ctx="${ctx}

Open PRs:
${open_prs}"

json_ctx=$(printf '%s' "$ctx" | python3 -c 'import sys, json; print(json.dumps(sys.stdin.read()))')

cat <<EOF
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ${json_ctx}}}
EOF
