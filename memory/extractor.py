"""Claude Code Stop hook: extract memories from a session transcript.

Invoked by Claude Code's Stop hook machinery with the session event on stdin.
Reads the transcript, calls Claude to extract structured memories, persists
them to Lakebase, then regenerates the local MEMORY.md file for the next session.

Run as: uv run python -m memory.extractor   (stdin = Claude Code hook JSON event)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Minimum transcript length (chars) worth processing
_MIN_TRANSCRIPT_LEN = 300

# How many memories to extract per session
_EXTRACTION_PROMPT = """\
You are analyzing a Claude Code coding session transcript to extract memories worth keeping for future sessions.

Extract 3–8 memories across these categories:
- "user": preferences, expertise level, workflow habits, communication style
- "feedback": explicit corrections, things to avoid, approaches the user validated ("yes exactly")
- "project": key decisions, architecture facts, goals, deadlines, tech stack choices for this project
- "reference": pointers to docs, tools, repos, or URLs discovered during the session

Return ONLY a JSON array — no prose, no markdown fences:
[
  {"type": "feedback", "content": "User prefers uv over pip for all Python deps", "importance": 0.9},
  {"type": "project", "content": "This project uses FastAPI + React with Vite", "importance": 0.7}
]

importance: 0.0–1.0  (1.0 = critical to remember, 0.5 = useful, 0.2 = minor)

Rules:
- Skip ephemeral details (error messages, file contents, one-off commands).
- Only record things that would meaningfully change how you approach the NEXT session.
- "feedback" memories should lead with the rule (NEVER/ALWAYS/PREFER), then WHY.
- Be concise — one sentence per memory.

Session transcript (most recent portion):
"""


def _render_message(role: str, content: Any) -> list[str]:
    """Flatten one message's content into transcript lines. Skips tool-use/result noise."""
    out: list[str] = []
    if isinstance(content, str):
        if content.strip():
            out.append(f"{role}: {content}")
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text.strip():
                    out.append(f"{role}: {text}")
            elif btype == "thinking":
                # Keep reasoning — often carries the "why" behind a decision
                text = block.get("thinking", "")
                if text.strip():
                    out.append(f"{role} (thinking): {text}")
            # Deliberately skip tool_use / tool_result — they're verbose and
            # add little signal for memory extraction.
    return out


def _parse_transcript(event: dict[str, Any]) -> str:
    """Extract a text representation of the session from the hook event.

    Claude Code writes JSONL lines as:
        {"type": "user"|"assistant", "message": {"role":..., "content":...}, ...}
    so we unwrap `message` before reading role/content. Falls back to the flat
    shape in case a different harness (or an embedded `transcript` array) is
    used.
    """
    lines: list[str] = []

    # Case 1: transcript embedded as array of messages (rare — path-ref is standard)
    for msg in event.get("transcript", []) or []:
        inner = msg.get("message", msg)
        lines.extend(_render_message(inner.get("role", ""), inner.get("content", "")))
    if lines:
        return "\n".join(lines)

    # Case 2: transcript_path points to a JSONL file (standard Claude Code shape)
    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        return ""
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return ""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        # Skip meta entries (summary, system init, etc.) — only user/assistant carry content
        entry_type = msg.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        inner = msg.get("message", {})
        if not isinstance(inner, dict):
            continue
        role = inner.get("role", entry_type)
        lines.extend(_render_message(role, inner.get("content", "")))

    return "\n".join(lines)


def _extract_with_claude(transcript_text: str) -> list[dict[str, Any]]:
    """Call Claude (via the configured Databricks endpoint) to extract memories."""
    import anthropic

    client = anthropic.Anthropic(
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        api_key=os.environ.get("ANTHROPIC_AUTH_TOKEN", "x"),
    )

    # Use the fastest/cheapest model — extraction is a simple structured task
    model = os.environ.get("MEMORY_EXTRACTION_MODEL", "claude-haiku-4-5-20251001")

    # Truncate to last 10k chars — recent context is most memory-worthy
    truncated = transcript_text[-10_000:]

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT + truncated}],
    )

    raw = response.content[0].text.strip()

    # Strip any accidental markdown code fences
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


_DEBUG_LOG = Path("/tmp/coda-stop-hook.log")


def _trace(msg: str) -> None:
    """Emit to stderr (visible inline during `claude -p`) and to a debug file
    so the full decision trace survives even when stderr is buffered or lost.
    Kept loud intentionally — silence has masked real failures here before."""
    line = f"[coda-memory] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{__import__('datetime').datetime.utcnow().isoformat()}Z {line}\n"
            )
    except Exception:
        pass


def stop_hook_handler() -> None:
    """Entry point for Claude Code's Stop hook."""
    _trace("stop-hook fired")

    if not os.environ.get("ENDPOINT_NAME"):
        _trace("skip: ENDPOINT_NAME not set")
        return

    owner_email = os.environ.get("APP_OWNER", "").strip()
    if not owner_email:
        _trace("skip: APP_OWNER not set")
        return

    raw_stdin = sys.stdin.read().strip()
    if not raw_stdin:
        _trace("skip: empty stdin")
        return

    try:
        event = json.loads(raw_stdin)
    except json.JSONDecodeError as e:
        _trace(f"bad hook event JSON: {e}")
        return

    session_id = event.get("session_id", "")
    cwd = event.get("cwd", "")
    project_name: str | None = Path(cwd).name if cwd else None
    _trace(f"session={session_id[:8]} cwd={cwd!r} project={project_name!r}")

    transcript_text = _parse_transcript(event)
    _trace(f"parsed transcript: {len(transcript_text)} chars")
    if len(transcript_text) < _MIN_TRANSCRIPT_LEN:
        _trace(
            f"skip: transcript too short ({len(transcript_text)} < {_MIN_TRANSCRIPT_LEN})"
        )
        return

    try:
        memories = _extract_with_claude(transcript_text)
    except Exception as e:
        _trace(f"extraction error: {type(e).__name__}: {e}")
        return

    if not isinstance(memories, list) or not memories:
        _trace(f"skip: no memories extracted (got {type(memories).__name__})")
        return
    _trace(f"extracted {len(memories)} memories")

    try:
        from memory.store import ensure_schema, write_memories

        ensure_schema()
        count = write_memories(memories, owner_email, project_name, session_id)
        _trace(f"stored {count} memories to Lakebase")
    except Exception as e:
        _trace(f"Lakebase write error: {type(e).__name__}: {e}")
        return

    # Splice the full memory set into ~/.claude/CLAUDE.md so the next Claude
    # session sees everything — cross-project lessons too, since the user may
    # cd anywhere. project_name=None pulls all of this owner's memories.
    try:
        from memory.injector import regenerate_memory_file

        path = regenerate_memory_file(owner_email, None)
        if path:
            _trace(f"memory file updated: {path}")
    except Exception as e:
        _trace(f"memory file update error: {e}")


if __name__ == "__main__":
    stop_hook_handler()
