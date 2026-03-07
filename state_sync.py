"""Bidirectional state sync between container and Databricks Workspace.

Persists Claude Code auto-memory, shell history, and other state files
to /Workspace/Users/{email}/.state/ so they survive container restarts.
"""

import os
import base64
import time
import threading
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)

# Timeout for individual Workspace API calls (seconds)
WORKSPACE_API_TIMEOUT = 30

# Max file size to sync (bytes) - prevents syncing huge files
MAX_SYNC_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Directories/files to sync (relative to HOME)
STATE_ITEMS = [
    # Claude Code auto-memory (glob pattern)
    ".claude/projects/*/memory",
    # Shell history
    ".bash_history",
]

# Workspace destination prefix (under user's home)
WORKSPACE_STATE_PREFIX = ".state"


def _get_home():
    home = os.environ.get("HOME", "/app/python/source_code")
    return home if home and home != "/" else "/app/python/source_code"


def _get_workspace_client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _get_user_email(w):
    return w.current_user.me().user_name


def _workspace_base(user_email):
    return f"/Workspace/Users/{user_email}/{WORKSPACE_STATE_PREFIX}"


def _collect_files(home):
    """Collect all files matching STATE_ITEMS patterns."""
    home_path = Path(home)
    files = []
    for pattern in STATE_ITEMS:
        if "*" in pattern:
            # Glob pattern — find matching directories/files
            for match in home_path.glob(pattern):
                if match.is_dir():
                    for f in match.rglob("*"):
                        if f.is_file():
                            files.append(f)
                elif match.is_file():
                    files.append(match)
        else:
            # Exact path
            p = home_path / pattern
            if p.is_file():
                files.append(p)
    return files


def save_state():
    """Upload state files to Databricks Workspace."""
    home = _get_home()
    try:
        w = _get_workspace_client()
        user_email = _get_user_email(w)
        base = _workspace_base(user_email)

        files = _collect_files(home)
        if not files:
            logger.info("State sync: no state files to save")
            return

        saved = 0
        for file_path in files:
            rel = file_path.relative_to(home)
            ws_path = f"{base}/{rel}"

            # Skip files that are too large
            file_size = file_path.stat().st_size
            if file_size > MAX_SYNC_FILE_SIZE:
                logger.warning(
                    f"State sync: skipping {rel} (size {file_size} exceeds {MAX_SYNC_FILE_SIZE})"
                )
                continue

            try:
                content = file_path.read_bytes()
                w.workspace.import_(
                    path=ws_path,
                    content=base64.b64encode(content).decode(),
                    format="AUTO",
                    overwrite=True,
                )
                saved += 1
            except Exception as e:
                logger.warning(f"State sync: failed to save {rel}: {e}")

        logger.info(f"State saved: {saved}/{len(files)} files to {base}")
    except Exception as e:
        logger.warning(f"State sync save failed: {e}")


def restore_state():
    """Download state files from Databricks Workspace."""
    home = _get_home()
    try:
        w = _get_workspace_client()
        user_email = _get_user_email(w)
        base = _workspace_base(user_email)

        # Check if state directory exists (with timeout)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(w.workspace.get_status, base)
                future.result(timeout=WORKSPACE_API_TIMEOUT)
        except FuturesTimeoutError:
            logger.warning(f"State sync: timeout checking {base}")
            return
        except Exception:
            logger.info("State sync: no saved state found (first run)")
            return

        restored = _restore_recursive(w, base, Path(home))
        logger.info(f"State restored: {restored} files from {base}")
    except Exception as e:
        logger.warning(f"State sync restore failed: {e}")


def _restore_recursive(w, ws_path, local_base):
    """Recursively download files from a workspace directory."""
    restored = 0
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: list(w.workspace.list(ws_path)))
            items = future.result(timeout=WORKSPACE_API_TIMEOUT)
    except (FuturesTimeoutError, Exception):
        return 0

    for item in items:
        # item.path is the full workspace path like /Workspace/Users/.../. state/...
        # We need the relative part after the .state/ prefix
        rel = item.path.split(f"/{WORKSPACE_STATE_PREFIX}/", 1)
        if len(rel) < 2:
            continue
        rel_path = rel[1]
        local_path = local_base / rel_path

        if item.object_type and item.object_type.value == "DIRECTORY":
            restored += _restore_recursive(w, item.path, local_base)
        else:
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        w.workspace.export, path=item.path, format="AUTO"
                    )
                    response = future.result(timeout=WORKSPACE_API_TIMEOUT)
                if response.content:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(base64.b64decode(response.content))
                    restored += 1
            except FuturesTimeoutError:
                logger.warning(f"State sync: timeout restoring {rel_path}")
            except Exception as e:
                logger.warning(f"State sync: failed to restore {rel_path}: {e}")

    return restored


def start_periodic_sync(interval=300):
    """Start a background thread that saves state every `interval` seconds."""

    def _sync_loop():
        while True:
            time.sleep(interval)
            try:
                save_state()
            except Exception as e:
                logger.warning(f"Periodic state sync error: {e}")

    thread = threading.Thread(target=_sync_loop, daemon=True, name="state-sync")
    thread.start()
    logger.info(f"Started periodic state sync (every {interval}s)")
    return thread
