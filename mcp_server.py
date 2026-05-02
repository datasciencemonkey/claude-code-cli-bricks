"""MCP server exposing CoDA session/task tools via FastMCP.

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
        "CoDA MCP server — create Hermes agent sessions, run coding tasks, "
        "poll status, retrieve results, and close sessions."
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
    - ``coda_create_session`` creates a PTY via ``create_session_fn(label=...)``
    - ``coda_run_task`` sends the hermes command via ``send_input_fn(pty_id, cmd)``
    - ``coda_close_session`` destroys the PTY via ``close_session_fn(pty_id)``

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
    - If found, calls ``task_manager.complete_task()``.
    - Tracks last activity from ``status.jsonl`` mtime.
    - Timeout: if wall clock exceeds *timeout_s* AND no status update
      in the last 5 minutes, writes a timeout result and completes.
    """
    tdir = task_manager._task_dir(session_id, task_id)
    result_path = os.path.join(tdir, "result.json")
    status_path = os.path.join(tdir, "status.jsonl")
    start = time.time()
    stale_threshold = 300  # 5 minutes

    while True:
        time.sleep(5)

        # Check for result.json
        if os.path.isfile(result_path):
            try:
                task_manager.complete_task(session_id, task_id)
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
                    task_manager._write_json(result_path, {
                        "summary": "Task timed out",
                        "files_changed": [],
                        "artifacts": [],
                        "errors": [f"Timeout after {timeout_s}s with no activity for 5 min"],
                    })
                    task_manager.complete_task(session_id, task_id)
                    logger.warning("Watcher: task %s timed out", task_id)
                except Exception:
                    logger.exception("Watcher: error timing out task %s", task_id)
                return


# ── Tool definitions ────────────────────────────────────────────────


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def coda_create_session(
    email: str,
    user_id: str = "",
    label: str = "",
) -> str:
    """Create a Hermes agent session.

    Returns JSON with ``session_id`` and ``status``.
    """
    try:
        result = task_manager.create_session(email, user_id, label)
        session_id = result["session_id"]

        # Create PTY if hooks are wired
        if _app_create_session is not None:
            pty_session_id = _app_create_session(label="hermes-mcp")
            task_manager._update_session_field(
                session_id, "pty_session_id", pty_session_id
            )

        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
    ),
)
async def coda_run_task(
    session_id: str,
    prompt: str,
    email: str,
    user_id: str = "",
    context: str = "{}",
    context_hint: str = "",
    timeout_s: int = 3600,
    permissions: str = "smart",
) -> str:
    """Send a coding task to Hermes in an existing session.

    ``context`` is a JSON string (MCP tools cannot accept dicts).
    ``permissions`` can be ``"smart"`` (default) or ``"yolo"`` (auto-approve).

    Returns JSON with ``task_id`` and ``status``.
    """
    try:
        # Parse context JSON
        try:
            ctx = json.loads(context) if context else None
        except json.JSONDecodeError:
            return json.dumps({
                "status": "error",
                "error": f"Invalid JSON in context parameter: {context!r}",
            })

        result = task_manager.create_task(
            session_id=session_id,
            prompt=prompt,
            email=email,
            context=ctx,
            context_hint=context_hint or None,
            timeout_s=timeout_s,
            permissions=permissions,
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

        return json.dumps(result)

    except task_manager.SessionBusyError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    except task_manager.SessionNotFoundError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
    ),
)
async def coda_get_status(
    task_id: str,
    session_id: str,
) -> str:
    """Poll task progress.

    Returns JSON with ``task_id``, ``status``, ``elapsed_s``, and
    optional ``progress`` fields.
    """
    try:
        status = task_manager.get_task_status(task_id, session_id)
        status["task_id"] = task_id

        # Add elapsed time if we have a timestamp
        if "ts" in status:
            status["elapsed_s"] = round(time.time() - status["ts"], 1)

        return json.dumps(status)
    except Exception as exc:
        return json.dumps({"status": "error", "task_id": task_id, "error": str(exc)})


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
    """Retrieve completed task result.

    Returns JSON with ``task_id``, ``status``, ``summary``,
    ``files_changed``, ``artifacts``, and ``errors``.
    """
    try:
        result = task_manager.get_task_result(task_id, session_id)
        if result is None:
            # No result yet — return current status
            status = task_manager.get_task_status(task_id, session_id)
            return json.dumps({
                "task_id": task_id,
                "status": status.get("status", "unknown"),
                "message": "Result not yet available — task is still in progress.",
            })

        result["task_id"] = task_id
        # Ensure standard fields exist
        result.setdefault("status", "done")
        result.setdefault("summary", "")
        result.setdefault("files_changed", [])
        result.setdefault("artifacts", [])
        result.setdefault("errors", [])
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"status": "error", "task_id": task_id, "error": str(exc)})


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
    ),
)
async def coda_close_session(
    session_id: str,
) -> str:
    """Close session and clean up.

    Returns JSON with ``session_id`` and ``status``.
    """
    try:
        # Close PTY if hooks are wired
        if _app_close_session is not None:
            try:
                session = task_manager._read_session(session_id)
                pty_session_id = session.get("pty_session_id")
                if pty_session_id:
                    _app_close_session(pty_session_id)
            except task_manager.SessionNotFoundError:
                pass  # session already gone — still try disk close below

        task_manager.close_session(session_id)
        return json.dumps({"session_id": session_id, "status": "closed"})
    except task_manager.SessionNotFoundError as exc:
        return json.dumps({"status": "error", "session_id": session_id, "error": str(exc)})
    except Exception as exc:
        return json.dumps({"status": "error", "session_id": session_id, "error": str(exc)})


# ── Standalone entry point ──────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
