"""Disk-based state manager for MCP sessions and tasks.

Pure Python module — no Flask dependency.  Just file I/O.

Layout on disk
--------------
~/.coda/sessions/{session-id}/
    session.json          – session metadata
    tasks/{task-id}/
        prompt.txt        – wrapped prompt sent to the agent
        meta.json         – task metadata (email, timestamps, chaining)
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

# ── Concurrency limit ───────────────────────────────────────────────

MAX_CONCURRENT_TASKS = int(os.environ.get("CODA_MAX_CONCURRENT", "5"))

# ── Task TTL (seconds) ──────────────────────────────────────────────

TASK_TTL_S = int(os.environ.get("CODA_TASK_TTL", str(24 * 3600)))  # 24h

# ── Exceptions ───────────────────────────────────────────────────────


class SessionBusyError(Exception):
    """Raised when a task is submitted to a session that already has one running."""


class SessionNotFoundError(Exception):
    """Raised when the requested session does not exist or is closed."""


class ConcurrencyLimitError(Exception):
    """Raised when MAX_CONCURRENT_TASKS running tasks already exist."""


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
    context_hint: str | None = None,
    previous_session_id: str | None = None,
) -> str:
    """Build the full prompt string written to ``prompt.txt``.

    Uses the ``---CODA-TASK---`` envelope convention so the agent can
    parse metadata from the prompt deterministically.
    """
    context_block = ""
    if context:
        context_block = f"\nCONTEXT:\n{json.dumps(context, indent=2)}\n"

    hint_line = ""
    if context_hint:
        hint_line = f"context_hint: {context_hint}\n"

    prior_session_block = ""
    if previous_session_id:
        prior_dir = _session_dir(previous_session_id)
        prior_session_block = (
            f"\nPRIOR SESSION: {previous_session_id}\n"
            f"Read {prior_dir}/tasks/*/result.json for context on prior work.\n"
        )

    return (
        f"---CODA-TASK---\n"
        f"task_id: {task_id}\n"
        f"session_id: {session_id}\n"
        f"user: {email}\n"
        f"{hint_line}"
        f"{prior_session_block}"
        f"{context_block}\n"
        f"TASK:\n"
        f"{prompt}\n"
        f"\n"
        f"INSTRUCTIONS:\n"
        f"1. As you work, append progress lines to {results_dir}/status.jsonl\n"
        f'   Each line must be valid JSON: {{"step": "label", "message": "what you are doing"}}\n'
        f"\n"
        f"2. When you are COMPLETELY DONE, write a SINGLE FILE at this exact path:\n"
        f"   {results_dir}/result.json\n"
        f"   It must contain this JSON structure:\n"
        f"   {{\n"
        f'     "status": "completed",\n'
        f'     "summary": "one paragraph describing what you did",\n'
        f'     "files_changed": ["list", "of", "file", "paths"],\n'
        f'     "artifacts": {{}},\n'
        f'     "errors": []\n'
        f"   }}\n"
        f"   If you failed, set status to \"failed\" and describe the error.\n"
        f"   IMPORTANT: result.json is a FILE not a directory. Write it with:\n"
        f"   echo '{{...}}' > {results_dir}/result.json\n"
        f"\n"
        f"3. If you delegate to a sub-agent, update status.jsonl with delegation steps.\n"
        f"---END-CODA-TASK---"
    )


# ── Task lifecycle ───────────────────────────────────────────────────


def create_task(
    session_id: str,
    prompt: str,
    email: str,
    context: dict | None = None,
    context_hint: str | None = None,
    timeout_s: int | None = None,
    permissions: str | None = None,
    previous_session_id: str | None = None,
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
        previous_session_id=previous_session_id,
    )
    with open(os.path.join(tdir, "prompt.txt"), "w") as f:
        f.write(wrapped)

    # Write meta.json for inbox scanning
    now = time.time()
    meta = {
        "email": email,
        "created_at": now,
        "previous_session_id": previous_session_id or "",
        "permissions": permissions or "smart",
        "timeout_s": timeout_s or 3600,
        "prompt_summary": prompt[:100],
    }
    _write_json(os.path.join(tdir, "meta.json"), meta)

    # Seed status log
    with open(os.path.join(tdir, "status.jsonl"), "w") as f:
        f.write(json.dumps({"status": "running", "ts": now}) + "\n")

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


def _find_result_json(task_dir: str) -> str | None:
    """Find result.json — agents may write it at root or in results/ subdir."""
    for candidate in [
        os.path.join(task_dir, "result.json"),
        os.path.join(task_dir, "results", "result.json"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def get_task_result(task_id: str, session_id: str) -> dict | None:
    """Read result.json if it exists; otherwise return None."""
    result_path = _find_result_json(_task_dir(session_id, task_id))
    if not result_path:
        return None
    try:
        with open(result_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# ── Task completion ──────────────────────────────────────────────────


def complete_task(session_id: str, task_id: str) -> None:
    """Mark a task as done and auto-close the session.

    Appends a ``done`` entry to status.jsonl, adds task_id to
    ``completed_tasks``, and closes the session (v2: ephemeral sessions).
    """
    session = _read_session(session_id)

    # Append done to status log
    status_path = os.path.join(_task_dir(session_id, task_id), "status.jsonl")
    with open(status_path, "a") as f:
        f.write(json.dumps({"status": "done", "ts": time.time()}) + "\n")

    # Update session — auto-close (v2: sessions are ephemeral)
    session["status"] = "closed"
    session["current_task"] = None
    session["closed_at"] = time.time()
    if task_id not in session["completed_tasks"]:
        session["completed_tasks"].append(task_id)
    _write_json(_session_file(session_id), session)

    logger.info("Completed task %s in session %s (auto-closed)", task_id, session_id)


# ── Inbox: list all tasks across sessions ───────────────────────────


def list_all_tasks(email: str = "", status_filter: str = "") -> list[dict]:
    """Scan all sessions and return a flat list of tasks for the inbox.

    Returns tasks from the last ``TASK_TTL_S`` seconds, sorted most recent first.
    Each entry includes task_id, session_id, status, elapsed_s, prompt_summary,
    summary (if completed), progress (if running), previous_session_id, created_at.
    """
    now = time.time()
    cutoff = now - TASK_TTL_S
    tasks = []

    if not os.path.isdir(SESSIONS_DIR):
        return tasks

    for sess_name in os.listdir(SESSIONS_DIR):
        sess_dir = os.path.join(SESSIONS_DIR, sess_name)
        if not os.path.isdir(sess_dir):
            continue

        tasks_dir = os.path.join(sess_dir, "tasks")
        if not os.path.isdir(tasks_dir):
            continue

        for task_name in os.listdir(tasks_dir):
            task_dir = os.path.join(tasks_dir, task_name)
            if not os.path.isdir(task_dir):
                continue

            # Read meta.json
            meta_path = os.path.join(task_dir, "meta.json")
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                # Legacy task without meta.json — skip or build minimal entry
                meta = {}

            created_at = meta.get("created_at", 0)
            if created_at < cutoff:
                continue

            # Filter by email
            if email and meta.get("email", "") != email:
                continue

            # Determine task status from status.jsonl
            task_status = _read_last_status(task_dir)

            # Check for result.json to determine completion
            result_path = _find_result_json(task_dir)
            summary = ""
            if result_path:
                try:
                    with open(result_path) as f:
                        result_data = json.load(f)
                    task_status = result_data.get("status", "completed")
                    summary = result_data.get("summary", "")
                except (OSError, json.JSONDecodeError):
                    pass

            # Filter by status
            if status_filter and task_status != status_filter:
                continue

            # Get progress for running tasks
            progress = ""
            if task_status == "running":
                progress = _read_last_progress(task_dir)

            elapsed_s = round(now - created_at, 1)

            entry = {
                "task_id": task_name,
                "session_id": sess_name,
                "status": task_status,
                "elapsed_s": elapsed_s,
                "prompt_summary": meta.get("prompt_summary", ""),
                "previous_session_id": meta.get("previous_session_id", ""),
                "created_at": created_at,
            }
            if summary:
                entry["summary"] = summary
            if progress:
                entry["progress"] = progress

            tasks.append(entry)

    # Sort most recent first
    tasks.sort(key=lambda t: t["created_at"], reverse=True)
    return tasks


def _read_last_status(task_dir: str) -> str:
    """Read the last status from status.jsonl."""
    status_path = os.path.join(task_dir, "status.jsonl")
    try:
        last = None
        with open(status_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)
        return (last or {}).get("status", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def _read_last_progress(task_dir: str) -> str:
    """Read the last progress message from status.jsonl."""
    status_path = os.path.join(task_dir, "status.jsonl")
    try:
        last = None
        with open(status_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)
        return (last or {}).get("message", "")
    except (OSError, json.JSONDecodeError):
        return ""


# ── Concurrency check ──────────────────────────────────────────────


def count_running_tasks() -> int:
    """Count tasks currently in 'running' state across all sessions."""
    count = 0
    if not os.path.isdir(SESSIONS_DIR):
        return count

    for sess_name in os.listdir(SESSIONS_DIR):
        sess_file = os.path.join(SESSIONS_DIR, sess_name, "session.json")
        try:
            with open(sess_file) as f:
                session = json.load(f)
            if session.get("status") == "busy":
                count += 1
        except (OSError, json.JSONDecodeError):
            continue
    return count


# ── Cleanup expired sessions ────────────────────────────────────────


def cleanup_expired_tasks() -> int:
    """Remove session directories older than TASK_TTL_S. Returns count removed."""
    import shutil

    now = time.time()
    cutoff = now - TASK_TTL_S
    removed = 0

    if not os.path.isdir(SESSIONS_DIR):
        return removed

    for sess_name in os.listdir(SESSIONS_DIR):
        sess_dir = os.path.join(SESSIONS_DIR, sess_name)
        if not os.path.isdir(sess_dir):
            continue

        sess_file = os.path.join(sess_dir, "session.json")
        try:
            with open(sess_file) as f:
                session = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        # Only clean closed sessions past TTL
        if session.get("status") != "closed":
            continue

        closed_at = session.get("closed_at", session.get("created_at", 0))
        if closed_at < cutoff:
            try:
                shutil.rmtree(sess_dir)
                removed += 1
                logger.info("Cleaned up expired session %s", sess_name)
            except OSError:
                logger.warning("Failed to clean up session %s", sess_name)

    return removed
