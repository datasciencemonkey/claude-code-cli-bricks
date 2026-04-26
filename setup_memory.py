"""Configure Lakebase-backed memory for Claude Code sessions.

Runs after setup_mlflow.py (sequential, not parallel) to avoid hook-merge
race conditions on settings.json.

On startup:
  1. Creates the coda_memories table if it doesn't exist.
  2. Regenerates the global coda_memory.md from Lakebase so memories from
     the last session are already in context when the user opens a new terminal.

Registers a Stop hook that:
  - Reads the session transcript.
  - Calls Claude (Haiku, cheap) to extract 3–8 structured memories.
  - Writes them to Lakebase.
  - Regenerates coda_memory.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])
settings_path = home / ".claude" / "settings.json"

# --- Guard: only proceed if Lakebase is configured ---
lakebase_host = os.environ.get("ENDPOINT_NAME", "").strip()
app_owner = os.environ.get("APP_OWNER", "").strip()

if not lakebase_host:
    print("CODA memory skipped: ENDPOINT_NAME not set")
    raise SystemExit(0)

if not app_owner:
    print("CODA memory skipped: APP_OWNER not set")
    raise SystemExit(0)

# --- Initialize schema + warm up memory file ---
try:
    from memory.store import ensure_schema
    ensure_schema()
    print("CODA memory: schema ready")
except Exception as e:
    print(f"CODA memory: schema init warning: {e}")

# Splice all of this user's Lakebase memories into ~/.claude/CLAUDE.md so
# they're available the moment a Claude session starts (Claude Code auto-loads
# that file). project_name=None means "all projects" — the user might `cd` to
# any of them, and cross-project lessons should be visible everywhere.
try:
    from memory.injector import regenerate_memory_file
    path = regenerate_memory_file(app_owner, None)
    if path:
        print(f"CODA memory: spliced into {path}")
    else:
        print("CODA memory: no memories yet (new instance)")
except Exception as e:
    print(f"CODA memory: memory file warning: {e}")

# --- Register hooks ---
# Use the absolute source dir so hooks work regardless of CWD when Claude fires them.
# Stop     → extract memories from transcript + write to Lakebase + regenerate MEMORY.md
# UserPromptSubmit → zero-cost nudge so Claude knows to invoke memory-recall subagent
app_dir = Path(__file__).parent.resolve()

stop_command = f"cd {app_dir} && uv run python -m memory.extractor"
nudge_command = f"cd {app_dir} && uv run python -m memory.hooks.user_prompt_submit"

if settings_path.exists():
    settings = json.loads(settings_path.read_text())
else:
    settings = {}

existing_hooks = settings.get("hooks", {})


def _has_command(entries: list[dict], cmd: str) -> bool:
    """True if `cmd` is already registered under any hook entry in `entries`.
    Keeps re-running setup_memory.py idempotent across container restarts."""
    for entry in entries:
        for h in entry.get("hooks", []) or []:
            if h.get("type") == "command" and h.get("command") == cmd:
                return True
    return False


stop_hooks = existing_hooks.get("Stop", [])
if _has_command(stop_hooks, stop_command):
    print(f"CODA memory: Stop hook already registered → {stop_command}")
else:
    stop_hooks.append({"hooks": [{"type": "command", "command": stop_command}]})
    print(f"CODA memory: Stop hook registered → {stop_command}")
existing_hooks["Stop"] = stop_hooks

nudge_hooks = existing_hooks.get("UserPromptSubmit", [])
if _has_command(nudge_hooks, nudge_command):
    print(f"CODA memory: UserPromptSubmit hook already registered → {nudge_command}")
else:
    nudge_hooks.append({"hooks": [{"type": "command", "command": nudge_command}]})
    print(f"CODA memory: UserPromptSubmit hook registered → {nudge_command}")
existing_hooks["UserPromptSubmit"] = nudge_hooks

settings["hooks"] = existing_hooks
settings_path.write_text(json.dumps(settings, indent=2))
