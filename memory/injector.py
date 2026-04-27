"""Inject Lakebase-backed memories into Claude Code's auto-loaded CLAUDE.md.

Stock Claude Code (used in CODA) auto-loads `CLAUDE.md` files from:
  - `./CLAUDE.md` and parents walked up (project-level)
  - `~/.claude/CLAUDE.md` (user global)

We write the rendered memories into `~/.claude/CLAUDE.md`, between explicit
markers so we can update just our section on each Stop hook without clobbering
any user-authored content above or below. The user-global path means memories
are visible in every Claude session regardless of cwd, which matches the way
Lakebase already aggregates rows across projects under `owner_email`.

(The `~/.claude/projects/<encoded>/memory/MEMORY.md` path some harnesses use
for auto-memory is NOT part of stock Claude Code 2.x — verified empirically
against `claude --version` 2.1.19 in the CODA container, which did not load
files from that path.)
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


_TYPE_HEADINGS = {
    "user": "## About You",
    "feedback": "## Preferences & Lessons Learned",
    "project": "## Project Context",
    "reference": "## References & Resources",
}

_CAP_PER_SECTION = 12

_BEGIN_MARKER = "<!-- BEGIN CODA MEMORY -->"
_END_MARKER = "<!-- END CODA MEMORY -->"

# Memory-injection defense: drop memories whose content reads as a directive
# rather than a passive observation. Rejection beats rewriting because a
# partially-stripped directive can still steer an LLM. URLs are tolerated
# only in "reference" memories (which legitimately record doc/repo pointers);
# shell-command patterns are dropped regardless of type. Logged to stderr.
_URL_RE = re.compile(r"https?://\S+")
_SHELL_CMD_RE = re.compile(
    r"\b(curl|wget|sh|bash|zsh|rm|chmod|sudo|export|eval|source)\s+\S+",
    re.IGNORECASE,
)


def _suspicious_flags(content: str, mem_type: str) -> list[str]:
    flags: list[str] = []
    if _SHELL_CMD_RE.search(content):
        flags.append("shell")
    if mem_type != "reference" and _URL_RE.search(content):
        flags.append("url")
    return flags


def _claude_md_path() -> Path:
    """Return the user-global CLAUDE.md path that Claude Code auto-loads."""
    home = Path(os.environ.get("HOME", "/app/python/source_code"))
    path = home / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _render_memory_section(memories: list[dict]) -> str:
    """Render the memories as a CLAUDE.md fragment between markers."""
    by_type: dict[str, list[dict]] = {}
    for mem in memories:
        by_type.setdefault(mem["type"], []).append(mem)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        _BEGIN_MARKER,
        "# CODA Memory",
        f"_Synced from Lakebase: {now}_",
        "",
        "These memories were extracted from past coding sessions and are stored in",
        "Lakebase for durability across app restarts and CODA instances.",
        "",
    ]
    for mem_type, heading in _TYPE_HEADINGS.items():
        items = by_type.get(mem_type, [])
        if not items:
            continue
        rendered: list[str] = []
        for item in items[:_CAP_PER_SECTION]:
            flags = _suspicious_flags(item["content"], mem_type)
            if flags:
                # Dropped, not rewritten: a partial sanitization could still
                # carry directive intent. Logged for observability.
                print(
                    f"[memory-injector] dropped {mem_type} memory "
                    f"({','.join(flags)}): {item['content'][:100]!r}",
                    file=sys.stderr,
                )
                continue
            project_tag = (
                f" _(project: {item['project_name']})_"
                if item.get("project_name")
                else ""
            )
            rendered.append(f"- {item['content']}{project_tag}")
        if not rendered:
            # Whole section was filtered out — skip the heading too rather than
            # leaving a bare heading with no items below it.
            continue
        lines.append(heading)
        lines.extend(rendered)
        lines.append("")
    lines.append(_END_MARKER)
    return "\n".join(lines)


def _splice_section(existing: str, new_section: str) -> str:
    """Replace any prior CODA-MEMORY section in `existing`, or append if absent."""
    if _BEGIN_MARKER in existing and _END_MARKER in existing:
        before, _, rest = existing.partition(_BEGIN_MARKER)
        _, _, after = rest.partition(_END_MARKER)
        return before.rstrip() + "\n\n" + new_section + after.lstrip("\n")
    sep = "\n\n" if existing and not existing.endswith("\n") else "\n"
    return existing + sep + new_section + "\n"


def regenerate_memory_file(
    owner_email: str,
    project_name: str | None,
    cwd: str | None = None,  # accepted for API compatibility; not used here
) -> Path | None:
    """Splice Lakebase-backed memories into `~/.claude/CLAUDE.md`.

    Returns the CLAUDE.md path on success, or None if there were no memories.
    """
    from memory.store import load_memories

    memories = load_memories(owner_email, project_name, limit=60)
    if not memories:
        return None

    new_section = _render_memory_section(memories)
    path = _claude_md_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(_splice_section(existing, new_section), encoding="utf-8")
    return path
