"""UserPromptSubmit hook: zero-cost nudge that memory search is available.

Claude Code fires this before processing each user prompt. We output a
systemMessage JSON string — Claude Code injects it as system context before
Claude sees the user's message. Zero Lakebase round-trips; pure signal.

Mirrors MemSearch's user-prompt-submit.sh pattern exactly.
"""
from __future__ import annotations

import json
import os
import sys

_NUDGE = json.dumps({
    "systemMessage": (
        "[coda-memory] Past session memories stored in Lakebase. "
        "Invoke the memory-recall subagent if historical context would help with this task."
    )
})

_SKIP_TERMS = ("memory-recall", "remember", "coda-memory")


def main() -> None:
    # Only nudge if Lakebase is actually configured
    if not os.environ.get("ENDPOINT_NAME"):
        sys.exit(0)

    raw = sys.stdin.read().strip()
    try:
        event = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        event = {}

    prompt = event.get("prompt", "")

    # Skip very short prompts and prompts that already reference memory
    if len(prompt) < 10:
        sys.exit(0)
    if any(term in prompt.lower() for term in _SKIP_TERMS):
        sys.exit(0)

    print(_NUDGE)


if __name__ == "__main__":
    main()
