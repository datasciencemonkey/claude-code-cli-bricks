"""CLI search module for the memory-recall subagent.

Called by the memory-recall agent as a subprocess:
    uv run python -m memory.searcher "query text here"
    uv run python -m memory.searcher "query" --project myproject --limit 8

Prints formatted markdown to stdout; agent reads and synthesises.
Exits with no output if Lakebase is unconfigured or no memories exist.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_TYPE_LABELS = {
    "user": "About You",
    "feedback": "Preference / Lesson",
    "project": "Project Context",
    "reference": "Reference",
}


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d")
    except Exception:
        return iso[:10]


def main() -> None:
    if not os.environ.get("ENDPOINT_NAME"):
        sys.exit(0)

    owner_email = os.environ.get("APP_OWNER", "").strip()
    if not owner_email:
        sys.exit(0)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=10)
    args, _ = parser.parse_known_args()

    query = args.query.strip()
    if not query:
        sys.exit(0)

    try:
        from memory.store import search_memories
        results = search_memories(
            owner_email=owner_email,
            query=query,
            project_name=args.project,
            limit=args.limit,
        )
    except Exception as e:
        print(f"[coda-memory] search error: {e}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("_No matching memories found._")
        return

    print(f"## Memory Search: `{query}`\n")
    for mem in results:
        label = _TYPE_LABELS.get(mem["type"], mem["type"].title())
        project_tag = f" · project: `{mem['project_name']}`" if mem.get("project_name") else ""
        date_tag = _fmt_date(mem["created_at"])
        print(f"**[{label}]** {mem['content']}")
        print(f"  _importance: {mem['importance']:.1f}{project_tag} · {date_tag}_\n")


if __name__ == "__main__":
    main()
