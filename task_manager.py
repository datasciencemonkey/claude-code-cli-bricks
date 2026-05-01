"""Disk-based state manager for MCP sessions and tasks.

Pure Python module — no Flask dependency.  Just file I/O.

Layout on disk
--------------
~/.coda/sessions/{session-id}/
    session.json          – session metadata
    tasks/{task-id}/
        prompt.txt        – wrapped prompt sent to the agent
        status.jsonl      – append-only progress log
        result.json       – final output (written by the agent)
"""

import json
import os
import secrets
import time
import logging

logger = logging.getLogger(__name__)

# ── Root directory (patched in tests) ────────────────────────────────

SESSIONS_DIR = os.path.join(
    os.environ.get("HOME", "/app/python/source_code"), ".coda", "sessions"
)

# ── Exceptions ───────────────────────────────────────────────────────


class SessionBusyError(Exception):
    """Raised when a task is submitted to a session that already has one running."""


class SessionNotFoundError(Exception):
    """Raised when the requested session does not exist or is closed."""


# ── ID generators ────────────────────────────────────────────────────


def _new_session_id() -> str:
    return f"sess-{secrets.token_hex(6)}"


def _new_task_id() -> str:
    return f"task-{secrets.token_hex(4)}"


# ── Low-level I/O ────────────────────────────────────────────────────


def _session_dir(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, session_id)


def _session_file(session_id: str) -> str:
    return os.path.join(_session_dir(session_id), "session.json")


def _task_dir(session_id: str, task_id: str) -> str:
    """Return the path to a task's directory."""
    return os.path.join(_session_dir(session_id), "tasks", task_id)


def _write_json(path: str, data: dict) -> None:
    """Atomic write via tmp-then-rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _read_session(session_id: str) -> dict:
    """Read session.json or raise SessionNotFoundError."""
    path = _session_file(session_id)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        raise SessionNotFoundError(f"Session {session_id} not found or corrupt")


def _update_session_field(session_id: str, key: str, value) -> None:
    """Update a single field in session.json (read-modify-write)."""
    data = _read_session(session_id)
    data[key] = value
    _write_json(_session_file(session_id), data)


# ── Session lifecycle ────────────────────────────────────────────────


def create_session(email: str, user_id: str, label: str = "") -> dict:
    """Create a new session directory with session.json.

    Returns ``{"session_id": "sess-…", "status": "ready"}``.
    """
    session_id = _new_session_id()
    data = {
        "session_id": session_id,
        "email": email,
        "user_id": user_id,
        "label": label,
        "status": "ready",
        "current_task": None,
        "completed_tasks": [],
        "created_at": time.time(),
    }
    _write_json(_session_file(session_id), data)
    logger.info("Created session %s for %s", session_id, email)
    return {"session_id": session_id, "status": "ready"}


def close_session(session_id: str) -> None:
    """Mark a session as closed.  Raises SessionNotFoundError if missing."""
    _read_session(session_id)  # existence check
    _update_session_field(session_id, "status", "closed")
    logger.info("Closed session %s", session_id)


# ── Prompt wrapping ──────────────────────────────────────────────────


def wrap_prompt(
    task_id: str,
    session_id: str,
    email: str,
    prompt: str,
    context: dict | None,
    results_dir: str,
    context_hint: str | None,
) -> str:
    """Build the full prompt string written to ``prompt.txt``.

    Uses the ``---CODA-TASK---`` envelope convention so the agent can
    parse metadata from the prompt deterministically.
    """
    parts = [
        "---CODA-TASK---",
        f"task_id: {task_id}",
        f"session_id: {session_id}",
        f"email: {email}",
        f"results_dir: {results_dir}",
    ]
    if context:
        parts.append(f"context: {json.dumps(context)}")
    if context_hint:
        parts.append(f"context_hint: {context_hint}")
    parts.append("---")
    parts.append(prompt)
    parts.append("---CODA-TASK---")
    return "\n".join(parts)


# ── Task lifecycle ───────────────────────────────────────────────────


def create_task(
    session_id: str,
    prompt: str,
    email: str,
    context: dict | None = None,
    context_hint: str | None = None,
    timeout_s: int | None = None,
    permissions: list | None = None,
) -> dict:
    """Create a task inside an existing session.

    Raises
    ------
    SessionNotFoundError
        If the session does not exist or is closed.
    SessionBusyError
        If the session already has a running task.

    Returns ``{"task_id": "task-…", "status": "running"}``.
    """
    session = _read_session(session_id)

    if session.get("status") == "closed":
        raise SessionNotFoundError(f"Session {session_id} is closed")

    if session.get("status") == "busy":
        raise SessionBusyError(
            f"Session {session_id} already has a running task: "
            f"{session.get('current_task')}"
        )

    task_id = _new_task_id()
    tdir = _task_dir(session_id, task_id)
    os.makedirs(tdir, exist_ok=True)

    # Write wrapped prompt
    results_dir = os.path.join(tdir, "results")
    wrapped = wrap_prompt(
        task_id=task_id,
        session_id=session_id,
        email=email,
        prompt=prompt,
        context=context,
        results_dir=results_dir,
        context_hint=context_hint,
    )
    with open(os.path.join(tdir, "prompt.txt"), "w") as f:
        f.write(wrapped)

    # Seed status log
    with open(os.path.join(tdir, "status.jsonl"), "w") as f:
        f.write(json.dumps({"status": "running", "ts": time.time()}) + "\n")

    # Mark session busy
    data = _read_session(session_id)
    data["status"] = "busy"
    data["current_task"] = task_id
    _write_json(_session_file(session_id), data)

    logger.info("Created task %s in session %s", task_id, session_id)
    return {"task_id": task_id, "status": "running"}


# ── Task queries ─────────────────────────────────────────────────────


def get_task_status(task_id: str, session_id: str) -> dict:
    """Read the last line of status.jsonl for the task.

    Returns ``{"status": "not_found"}`` if the task directory is missing.
    """
    status_path = os.path.join(_task_dir(session_id, task_id), "status.jsonl")
    try:
        last = None
        with open(status_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)
        return last or {"status": "not_found"}
    except (OSError, json.JSONDecodeError):
        return {"status": "not_found"}


def get_task_result(task_id: str, session_id: str) -> dict | None:
    """Read result.json if it exists; otherwise return None."""
    result_path = os.path.join(_task_dir(session_id, task_id), "result.json")
    try:
        with open(result_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ── Task completion ──────────────────────────────────────────────────


def complete_task(session_id: str, task_id: str) -> None:
    """Mark a task as done and return the session to ready.

    Appends a ``done`` entry to status.jsonl, clears ``current_task``,
    and adds the task_id to ``completed_tasks``.
    """
    session = _read_session(session_id)

    # Append done to status log
    status_path = os.path.join(_task_dir(session_id, task_id), "status.jsonl")
    with open(status_path, "a") as f:
        f.write(json.dumps({"status": "done", "ts": time.time()}) + "\n")

    # Update session
    session["status"] = "ready"
    session["current_task"] = None
    if task_id not in session["completed_tasks"]:
        session["completed_tasks"].append(task_id)
    _write_json(_session_file(session_id), session)

    logger.info("Completed task %s in session %s", task_id, session_id)
