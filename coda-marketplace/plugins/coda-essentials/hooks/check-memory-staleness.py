"""SessionStart hook: warn about stale Claude Code memory files.

Scans ~/.claude/projects/*/memory/ and reports entries whose frontmatter
`last_verified` is missing or older than the threshold (default 30 days).

Exit 0 = clean, exit 1 = stale memories found (warnings on stdout).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
DEFAULT_STALE_DAYS = 30

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
LAST_VERIFIED_RE = re.compile(r"^last_verified:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
NAME_RE = re.compile(r"^name:\s*(.+)", re.MULTILINE)
TYPE_RE = re.compile(r"^type:\s*(.+)", re.MULTILINE)


def cwd_to_project_slug(cwd: str) -> str:
    return re.sub(r"[/.]", "-", cwd)


def slug_to_readable(slug: str) -> str:
    home = str(Path.home())
    home_slug = re.sub(r"[/.]", "-", home)
    if slug.startswith(home_slug):
        return "~" + slug[len(home_slug):].replace("-", "/")
    return slug.lstrip("-").replace("-", "/")


def parse_memory(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    m = FRONTMATTER_RE.search(text)
    if not m:
        return None
    fm = m.group(1)
    name = NAME_RE.search(fm)
    verified = LAST_VERIFIED_RE.search(fm)
    type_ = TYPE_RE.search(fm)
    return {
        "path": path,
        "name": name.group(1).strip() if name else path.stem,
        "type": type_.group(1).strip() if type_ else "unknown",
        "last_verified": verified.group(1) if verified else None,
    }


def check_staleness(threshold_days: int, project_slug: str | None) -> list[dict]:
    if not PROJECTS_DIR.exists():
        return []
    today = date.today()
    threshold = today - timedelta(days=threshold_days)
    stale: list[dict] = []
    dirs = [PROJECTS_DIR / project_slug / "memory"] if project_slug \
        else sorted(PROJECTS_DIR.glob("*/memory"))
    for memory_dir in dirs:
        if not memory_dir.exists():
            continue
        proj = memory_dir.parent.name
        for md in sorted(memory_dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            info = parse_memory(md)
            if info is None:
                continue
            if info["last_verified"] is None:
                stale.append({
                    "project": proj, "name": info["name"], "type": info["type"],
                    "reason": "missing last_verified", "file": str(md),
                })
                continue
            try:
                vdate = date.fromisoformat(info["last_verified"])
            except ValueError:
                stale.append({
                    "project": proj, "name": info["name"], "type": info["type"],
                    "reason": f"invalid date: {info['last_verified']}",
                    "file": str(md),
                })
                continue
            if vdate < threshold:
                age = (today - vdate).days
                stale.append({
                    "project": proj, "name": info["name"], "type": info["type"],
                    "reason": f"{age}d since verified ({info['last_verified']})",
                    "file": str(md),
                })
    return stale


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd")
    parser.add_argument("--days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    slug = cwd_to_project_slug(args.cwd) if args.cwd and not args.all else None
    stale = check_staleness(args.days, slug)
    if not stale:
        return 0

    by_proj: dict[str, list[dict]] = {}
    for e in stale:
        by_proj.setdefault(e["project"], []).append(e)

    total = len(stale)
    if slug:
        lines = [f"Stale memories ({total}) in {slug_to_readable(slug)}:"]
        for e in stale:
            lines.append(f"  - [{e['type']}] {e['name']}: {e['reason']}")
    else:
        lines = [f"Stale memories: {total} across {len(by_proj)} project(s)"]
        for proj, entries in by_proj.items():
            lines.append(f"  {slug_to_readable(proj)}: {len(entries)} stale")
            for e in entries:
                lines.append(f"    - [{e['type']}] {e['name']}: {e['reason']}")
    lines.append("\nUpdate `last_verified` to today's date after reviewing each memory.")
    print("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
