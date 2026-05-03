"""MCP server exposing CoDA session/task tools via FastMCP.

v2: Background execution + inbox pattern.
- ``coda_run`` — fire-and-forget task submission (auto-creates ephemeral session)
- ``coda_inbox`` — dashboard of all background tasks
- ``coda_get_result`` — pull full structured result for a completed task

Delegates all disk state to ``task_manager.py``.  PTY operations are
handled through optional app hooks set via ``set_app_hooks()``.

Run standalone for testing::

    python mcp_server.py          # stdio transport
"""

import json
import logging
import os
import threading
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from mcp.types import ToolAnnotations

import task_manager

logger = logging.getLogger(__name__)

# ── FastMCP instance ────────────────────────────────────────────────

# Build allowed origins from DATABRICKS_HOST for Genie Code requests
_databricks_host = os.environ.get("DATABRICKS_HOST", "")
_allowed_origins = []
if _databricks_host:
    # Ensure https:// prefix, strip trailing slash
    origin = _databricks_host if _databricks_host.startswith("https://") else f"https://{_databricks_host}"
    _allowed_origins.append(origin.rstrip("/"))

mcp = FastMCP(
    "coda",
    instructions=(
        "CoDA MCP server — delegate coding tasks to AI agents on Databricks. "
        "Workflow: 1) coda_run to submit work (returns immediately, runs in background), "
        "2) continue your conversation — the task runs independently, "
        "3) when the user asks about background work, or you want to check progress, "
        "call coda_inbox — it shows ALL tasks (running, completed, failed) from the last 24h. "
        "Use status filter to narrow: coda_inbox(status='running') for pending work only. "
        "4) for completed tasks, call coda_get_result for full structured output. "
        "To chain work: pass previous_session_id from a completed task's session_id "
        "to give the new task context of what was done before."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# ── App hooks (PTY integration) ─────────────────────────────────────

_app_create_session = None
_app_send_input = None
_app_close_session = None


def set_app_hooks(create_session_fn, send_input_fn, close_session_fn):
    """Wire up Flask app callbacks for PTY operations.

    When hooks are set:
    - ``coda_run`` creates a PTY via ``create_session_fn(label=...)``
    - ``coda_run`` sends the hermes command via ``send_input_fn(pty_id, cmd)``
    - Task completion destroys the PTY via ``close_session_fn(pty_id)``

    When hooks are *not* set (e.g. in tests), only disk state is managed.
    """
    global _app_create_session, _app_send_input, _app_close_session
    _app_create_session = create_session_fn
    _app_send_input = send_input_fn
    _app_close_session = close_session_fn


# ── Background watcher ──────────────────────────────────────────────


def _watch_task(session_id: str, task_id: str, timeout_s: int) -> None:
    """Poll for result.json in a daemon thread.

    - Checks every 5 seconds for ``result.json`` in the task directory.
    - If found, calls ``task_manager.complete_task()`` (which auto-closes session).
    - Tracks last activity from ``status.jsonl`` mtime.
    - Timeout: if wall clock exceeds *timeout_s* AND no status update
      in the last 5 minutes, writes a timeout result and completes.
    - On completion, closes the PTY if hooks are wired.
    """
    tdir = task_manager._task_dir(session_id, task_id)
    status_path = os.path.join(tdir, "status.jsonl")
    start = time.time()
    stale_threshold = 300  # 5 minutes

    while True:
        time.sleep(5)

        # Check for result.json (may be at root or in results/ subdir)
        result_path = task_manager._find_result_json(tdir)
        if result_path:
            try:
                task_manager.complete_task(session_id, task_id)
                _close_pty_for_session(session_id)
                logger.info("Watcher: task %s completed (result found)", task_id)
            except Exception:
                logger.exception("Watcher: error completing task %s", task_id)
            return

        # Check timeout
        elapsed = time.time() - start
        if elapsed > timeout_s:
            # Check last activity
            try:
                last_activity = os.path.getmtime(status_path)
            except OSError:
                last_activity = start

            if (time.time() - last_activity) > stale_threshold:
                # Write timeout result and complete
                try:
                    timeout_result_path = os.path.join(tdir, "result.json")
                    task_manager._write_json(timeout_result_path, {
                        "status": "timeout",
                        "summary": "Task timed out",
                        "files_changed": [],
                        "artifacts": [],
                        "errors": [f"Timeout after {timeout_s}s with no activity for 5 min"],
                    })
                    task_manager.complete_task(session_id, task_id)
                    _close_pty_for_session(session_id)
                    logger.warning("Watcher: task %s timed out", task_id)
                except Exception:
                    logger.exception("Watcher: error timing out task %s", task_id)
                return


def _close_pty_for_session(session_id: str) -> None:
    """Close the PTY associated with a session, if hooks are wired."""
    if _app_close_session is None:
        return
    try:
        session = task_manager._read_session(session_id)
        pty_session_id = session.get("pty_session_id")
        if pty_session_id:
            _app_close_session(pty_session_id)
    except Exception:
        logger.debug("Could not close PTY for session %s", session_id, exc_info=True)


# ── Tool definitions ────────────────────────────────────────────────


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def coda_run(
    prompt: str,
    email: str,
    context: str = "{}",
    previous_session_id: str = "",
    permissions: str = "smart",
    timeout_s: int = 3600,
) -> str:
    """Submit a coding task to run in the background.

    Returns IMMEDIATELY with a task_id and session_id while agents work
    in the background. Do NOT poll — use coda_inbox to check all tasks at once.

    ``context`` is a JSON string with Unity Catalog metadata (tables, schemas).
    ``previous_session_id`` chains to a prior task's session for context continuity.
    ``permissions`` can be ``"smart"`` (default, safe) or ``"yolo"`` (auto-approve all).

    Returns JSON with ``task_id``, ``session_id``, and ``status: "running"``.
    """
    try:
        # Check concurrency limit
        running = task_manager.count_running_tasks()
        if running >= task_manager.MAX_CONCURRENT_TASKS:
            return json.dumps({
                "status": "error",
                "error": f"Concurrency limit reached ({task_manager.MAX_CONCURRENT_TASKS} "
                         f"tasks running). Try again when a task completes.",
            })

        # Parse context JSON
        try:
            ctx = json.loads(context) if context else None
        except json.JSONDecodeError:
            return json.dumps({
                "status": "error",
                "error": f"Invalid JSON in context parameter: {context!r}",
            })

        # Auto-create ephemeral session
        session_result = task_manager.create_session(email, "", label="hermes-mcp")
        session_id = session_result["session_id"]

        # Create PTY if hooks are wired
        if _app_create_session is not None:
            pty_session_id = _app_create_session(label="hermes-mcp")
            task_manager._update_session_field(
                session_id, "pty_session_id", pty_session_id
            )

        # Create task with chaining support
        result = task_manager.create_task(
            session_id=session_id,
            prompt=prompt,
            email=email,
            context=ctx,
            timeout_s=timeout_s,
            permissions=permissions,
            previous_session_id=previous_session_id or None,
        )
        task_id = result["task_id"]

        # Send to PTY if hooks are wired
        if _app_send_input is not None:
            session = task_manager._read_session(session_id)
            pty_session_id = session.get("pty_session_id")
            if pty_session_id:
                # Build hermes command
                tdir = task_manager._task_dir(session_id, task_id)
                prompt_path = os.path.join(tdir, "prompt.txt")
                cmd = f'hermes -z "{prompt_path}"'
                if permissions == "yolo":
                    cmd += " --yolo"
                cmd += "\n"

                _app_send_input(pty_session_id, cmd)

                # Start background watcher
                t = threading.Thread(
                    target=_watch_task,
                    args=(session_id, task_id, timeout_s),
                    daemon=True,
                )
                t.start()

        return json.dumps({
            "task_id": task_id,
            "session_id": session_id,
            "status": "running",
        })

    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def coda_inbox(
    email: str = "",
    status: str = "",
) -> str:
    """Check status of all background tasks — your inbox.

    Call this instead of polling — it returns ALL tasks at once.
    No need to track individual task_ids; the inbox shows everything
    from the last 24 hours: running, completed, and failed tasks.

    By default returns all tasks. Filter by ``status`` to narrow:
    ``"running"`` for in-progress only, ``"completed"`` for finished,
    ``"failed"`` for errors, or ``""`` (default) for everything.

    Each task includes: ``task_id``, ``session_id``, ``status``,
    ``elapsed_s``, ``prompt_summary`` (first 100 chars of what was asked),
    ``previous_session_id`` (if chained from prior work).
    Completed tasks also include ``summary`` (what was done).
    Running tasks also include ``progress`` (latest agent step).

    Returns JSON with ``tasks`` (list sorted most recent first)
    and ``counts`` (e.g. ``{"running": 1, "completed": 2, "failed": 0}``).
    """
    try:
        tasks = task_manager.list_all_tasks(email=email, status_filter=status)

        counts = {"running": 0, "completed": 0, "failed": 0}
        for t in tasks:
            s = t.get("status", "")
            if s in counts:
                counts[s] += 1
            elif s == "done":
                counts["completed"] += 1
            elif s == "timeout":
                counts["failed"] += 1

        return json.dumps({"tasks": tasks, "counts": counts})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def coda_get_result(
    task_id: str,
    session_id: str,
) -> str:
    """Retrieve the structured result of a completed task.

    Call this AFTER coda_inbox shows a task as "completed" or "failed".

    Returns JSON with ``task_id``, ``session_id``, ``status``, ``summary``
    (what was done), ``files_changed`` (list of modified files),
    ``artifacts`` (job IDs, commit hashes, etc.), and ``errors`` (if any).
    """
    try:
        result = task_manager.get_task_result(task_id, session_id)
        if result is None:
            # No result yet — return current status
            status = task_manager.get_task_status(task_id, session_id)
            return json.dumps({
                "task_id": task_id,
                "session_id": session_id,
                "status": status.get("status", "unknown"),
                "message": "Result not yet available — task is still in progress.",
            })

        result["task_id"] = task_id
        result["session_id"] = session_id
        # Ensure standard fields exist
        result.setdefault("status", "done")
        result.setdefault("summary", "")
        result.setdefault("files_changed", [])
        result.setdefault("artifacts", [])
        result.setdefault("errors", [])
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"status": "error", "task_id": task_id, "error": str(exc)})


# ── Standalone entry point ──────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
