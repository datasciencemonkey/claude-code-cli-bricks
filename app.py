import os
import pty
import fcntl
import struct
import termios
import select
import subprocess
import uuid
import threading
import signal
import time
import copy
import logging
import sys
from flask import Flask, send_from_directory, request, jsonify, session
from collections import deque

from utils import ensure_https, resolve_auth, AuthMode, TokenRefresher

# Session timeout configuration
SESSION_TIMEOUT_SECONDS = 60        # No poll for 60s = dead session
CLEANUP_INTERVAL_SECONDS = 30       # How often to check for stale sessions
GRACEFUL_SHUTDOWN_WAIT = 3          # Seconds to wait after SIGHUP before SIGKILL

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.urandom(24)

# Store sessions: {session_id: {"master_fd": fd, "pid": pid, "output_buffer": deque}}
sessions = {}
sessions_lock = threading.Lock()

# Setup state tracking
setup_lock = threading.Lock()
setup_state = {
    "status": "pending",
    "started_at": None,
    "completed_at": None,
    "error": None,
    "steps": [
        {"id": "git",        "label": "Configuring git identity",     "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "micro",      "label": "Installing micro editor",      "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "gh",         "label": "Installing GitHub CLI",        "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "claude",     "label": "Configuring Claude CLI",       "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "codex",      "label": "Configuring Codex CLI",        "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "opencode",   "label": "Configuring OpenCode CLI",     "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "gemini",     "label": "Configuring Gemini CLI",       "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "databricks", "label": "Setting up Databricks CLI",    "status": "pending", "started_at": None, "completed_at": None, "error": None},
        {"id": "git_clone",  "label": "Cloning git repositories",     "status": "pending", "started_at": None, "completed_at": None, "error": None},
    ]
}


def _update_step(step_id, **kwargs):
    with setup_lock:
        for step in setup_state["steps"]:
            if step["id"] == step_id:
                step.update(kwargs)
                break


def _get_setup_state_snapshot():
    with setup_lock:
        return copy.deepcopy(setup_state)


# Single-user security: only the token owner can access the terminal
app_owner = None
# Token refresher for OAuth M2M mode
token_refresher = None


def _run_step(step_id, command):
    _update_step(step_id, status="running", started_at=time.time())
    try:
        env = os.environ.copy()
        if not env.get("HOME") or env["HOME"] == "/":
            env["HOME"] = "/app/python/source_code"

        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            _update_step(step_id, status="complete", completed_at=time.time())
        else:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            _update_step(step_id, status="error", completed_at=time.time(), error=err[:500])
    except subprocess.TimeoutExpired:
        _update_step(step_id, status="error", completed_at=time.time(), error="Timed out after 300s")
    except Exception as e:
        _update_step(step_id, status="error", completed_at=time.time(), error=str(e))


def _setup_git_config():
    """Configure git identity and hooks by writing files directly (no subprocess)."""
    home = os.environ.get("HOME", "/app/python/source_code")
    if not home or home == "/":
        home = "/app/python/source_code"

    # Get user identity from Databricks credentials (PAT or OAuth M2M)
    user_email = None
    display_name = None
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        me = w.current_user.me()
        user_email = me.user_name
        display_name = me.display_name or user_email.split("@")[0]
    except Exception as e:
        logger.warning(f"Could not get user identity: {e}")

    # Write ~/.gitconfig directly (more reliable than subprocess git config)
    gitconfig_path = os.path.join(home, ".gitconfig")
    hooks_dir = os.path.join(home, ".githooks")
    os.makedirs(hooks_dir, exist_ok=True)

    # Write git credential helper script
    local_bin = os.path.join(home, ".local", "bin")
    os.makedirs(local_bin, exist_ok=True)
    credential_helper_path = os.path.join(local_bin, "git-credential-databricks")
    with open(credential_helper_path, "w") as f:
        f.write('#!/bin/bash\n')
        f.write('# Git credential helper: host-aware, supports both enterprise git and Databricks.\n')
        f.write('# Implements the git credential helper protocol.\n')
        f.write('#\n')
        f.write('# GIT_TOKEN + GIT_TOKEN_HOST → used for matching hosts (GitHub, Azure DevOps, GitLab)\n')
        f.write('# DATABRICKS_TOKEN → fallback for Databricks-hosted git and other hosts\n')
        f.write('\n')
        f.write('# Only respond to "get" action; silently ignore store/erase.\n')
        f.write('if [ "$1" != "get" ]; then\n')
        f.write('    exit 0\n')
        f.write('fi\n')
        f.write('\n')
        f.write('# Read stdin to extract the host being requested.\n')
        f.write('REQ_HOST=""\n')
        f.write('while IFS= read -r line; do\n')
        f.write('    [ -z "$line" ] && break\n')
        f.write('    case "$line" in\n')
        f.write('        host=*) REQ_HOST="${line#host=}" ;;\n')
        f.write('    esac\n')
        f.write('done\n')
        f.write('\n')
        f.write('# If GIT_TOKEN is set, use it for matching hosts (or all hosts if GIT_TOKEN_HOST is unset).\n')
        f.write('if [ -n "$GIT_TOKEN" ]; then\n')
        f.write('    if [ -z "$GIT_TOKEN_HOST" ] || echo "$REQ_HOST" | grep -qi "$GIT_TOKEN_HOST"; then\n')
        f.write('        printf "username=token\\npassword=%s\\n" "$GIT_TOKEN"\n')
        f.write('        exit 0\n')
        f.write('    fi\n')
        f.write('fi\n')
        f.write('\n')
        f.write('# Fallback to DATABRICKS_TOKEN for Databricks-hosted git and other hosts.\n')
        f.write('if [ -n "$DATABRICKS_TOKEN" ]; then\n')
        f.write('    printf "username=token\\npassword=%s\\n" "$DATABRICKS_TOKEN"\n')
        f.write('    exit 0\n')
        f.write('fi\n')
        f.write('\n')
        f.write('exit 1\n')
    os.chmod(credential_helper_path, 0o755)
    logger.info(f"Git credential helper written to {credential_helper_path}")

    lines = []
    if user_email and display_name:
        lines.append("[user]")
        lines.append(f"\temail = {user_email}")
        lines.append(f"\tname = {display_name}")
    lines.append("[core]")
    lines.append(f"\thooksPath = {hooks_dir}")
    lines.append("[credential]")
    lines.append(f"\thelper = {credential_helper_path}")

    with open(gitconfig_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    logger.info(f"Git config written to {gitconfig_path}")

    # Post-commit hook: workspace sync (opt-in) or just a placeholder
    post_commit = os.path.join(hooks_dir, "post-commit")
    workspace_sync = os.environ.get("WORKSPACE_SYNC", "").lower() in ("1", "true", "yes")

    with open(post_commit, "w") as f:
        f.write('#!/bin/bash\n')
        if workspace_sync:
            f.write('# Auto-sync to Databricks Workspace on commit (WORKSPACE_SYNC=true)\n')
            f.write('SYNC_LOG="$HOME/.sync.log"\n')
            f.write('\n')
            f.write('REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"\n')
            f.write('if [ -z "$REPO_ROOT" ]; then\n')
            f.write('    echo "[post-commit] $(date +%H:%M:%S) SKIP: not inside a git repo" >> "$SYNC_LOG"\n')
            f.write('    exit 0\n')
            f.write('fi\n')
            f.write('\n')
            f.write('PROJECTS_DIR="$HOME/projects"\n')
            f.write('case "$REPO_ROOT" in\n')
            f.write('    "$PROJECTS_DIR"/*)\n')
            f.write('        ;; # allowed - continue\n')
            f.write('    *)\n')
            f.write('        echo "[post-commit] $(date +%H:%M:%S) SKIP: $REPO_ROOT is outside $PROJECTS_DIR" >> "$SYNC_LOG"\n')
            f.write('        exit 0\n')
            f.write('        ;;\n')
            f.write('esac\n')
            f.write('\n')
            f.write('echo "[post-commit] $(date +%H:%M:%S) syncing $REPO_ROOT" >> "$SYNC_LOG"\n')
            f.write('\n')
            f.write('VENV_PYTHON="/app/python/source_code/.venv/bin/python"\n')
            f.write('SYNC_SCRIPT="/app/python/source_code/sync_to_workspace.py"\n')
            f.write('\n')
            f.write('if [ -x "$VENV_PYTHON" ] && [ -f "$SYNC_SCRIPT" ]; then\n')
            f.write('    nohup "$VENV_PYTHON" "$SYNC_SCRIPT" "$REPO_ROOT" >> "$SYNC_LOG" 2>&1 & disown\n')
            f.write('else\n')
            f.write('    echo "[post-commit] $(date +%H:%M:%S) SKIP: venv=$VENV_PYTHON script=$SYNC_SCRIPT" >> "$SYNC_LOG"\n')
            f.write('fi\n')
        else:
            f.write('# Workspace sync disabled (set WORKSPACE_SYNC=true to enable)\n')
            f.write('exit 0\n')
    os.chmod(post_commit, 0o755)
    logger.info(f"Post-commit hook written to {post_commit}")


def _clone_git_repos():
    """Clone repos listed in GIT_REPOS env var into ~/projects/."""
    git_repos = os.environ.get("GIT_REPOS", "").strip()
    if not git_repos:
        _update_step("git_clone", status="complete", completed_at=time.time())
        return

    _update_step("git_clone", status="running", started_at=time.time())
    home = os.environ.get("HOME", "/app/python/source_code")
    projects_dir = os.path.join(home, "projects")
    os.makedirs(projects_dir, exist_ok=True)

    repos = [r.strip() for r in git_repos.split(",") if r.strip()]
    errors = []

    for repo_url in repos:
        # Derive folder name from URL: https://github.com/org/repo.git → repo
        repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        target_dir = os.path.join(projects_dir, repo_name)

        if os.path.isdir(target_dir):
            logger.info(f"Repo already exists, skipping: {target_dir}")
            continue

        logger.info(f"Cloning {repo_url} into {target_dir}")
        try:
            result = subprocess.run(
                ["git", "clone", repo_url, target_dir],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                err = result.stderr.strip() or "clone failed"
                errors.append(f"{repo_name}: {err}")
                logger.error(f"Failed to clone {repo_url}: {err}")
            else:
                logger.info(f"Cloned {repo_url}")
        except subprocess.TimeoutExpired:
            errors.append(f"{repo_name}: timed out after 120s")
        except Exception as e:
            errors.append(f"{repo_name}: {e}")

    if errors:
        _update_step("git_clone", status="error", completed_at=time.time(),
                      error="; ".join(errors)[:500])
    else:
        _update_step("git_clone", status="complete", completed_at=time.time())


def run_setup():
    with setup_lock:
        setup_state["status"] = "running"
        setup_state["started_at"] = time.time()

    # Git config — done directly in Python, not as a subprocess
    _update_step("git", status="running", started_at=time.time())
    try:
        _setup_git_config()
        _update_step("git", status="complete", completed_at=time.time())
    except Exception as e:
        _update_step("git", status="error", completed_at=time.time(), error=str(e))

    _run_step("micro", ["bash", "-c",
        "mkdir -p ~/.local/bin && bash install_micro.sh && mv micro ~/.local/bin/ 2>/dev/null || true"])
    _run_step("gh", ["bash", "-c",
        'GH_VERSION="2.74.1" && '
        'mkdir -p ~/.local/bin && '
        'curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o /tmp/gh.tar.gz && '
        'tar -xzf /tmp/gh.tar.gz -C /tmp && '
        'mv /tmp/gh_${GH_VERSION}_linux_amd64/bin/gh ~/.local/bin/gh && '
        'rm -rf /tmp/gh.tar.gz /tmp/gh_${GH_VERSION}_linux_amd64 && '
        'chmod +x ~/.local/bin/gh && '
        # Configure gh to use git's credential protocol instead of its own
        'gh config set git_protocol https 2>/dev/null || true && '
        # Wrap gh to auto-add flags that skip interactive prompts (arrow-key menus break in xterm.js PTY)
        # The PTY sends OSC escape sequences that corrupt gh's interactive prompt library,
        # so we pipe "Y" to answer the git-credential prompt non-interactively.
        'printf \'#!/bin/bash\\n'
        'if [ "$1" = "auth" ] && [ "$2" = "login" ]; then\\n'
        '    shift 2\\n'
        '    printf "Y\\\\n" | ~/.local/bin/gh.real auth login -h github.com -p https -w --skip-ssh-key "$@"\\n'
        'fi\\n'
        'exec ~/.local/bin/gh.real "$@"\\n\' > ~/.local/bin/gh.wrapper && '
        'mv ~/.local/bin/gh ~/.local/bin/gh.real && '
        'mv ~/.local/bin/gh.wrapper ~/.local/bin/gh && '
        'chmod +x ~/.local/bin/gh'])
    # Use the currently running interpreter instead of assuming `python` exists in PATH.
    py = sys.executable or "python"
    _run_step("claude", [py, "setup_claude.py"])
    _run_step("codex", [py, "setup_codex.py"])
    _run_step("opencode", [py, "setup_opencode.py"])
    _run_step("gemini", [py, "setup_gemini.py"])
    _run_step("databricks", [py, "setup_databricks.py"])

    # Clone git repos specified in GIT_REPOS env var
    _clone_git_repos()

    with setup_lock:
        any_error = any(s["status"] == "error" for s in setup_state["steps"])
        setup_state["status"] = "error" if any_error else "complete"
        setup_state["completed_at"] = time.time()


def _get_app_owner(auth):
    """Get the owner email for authorization.

    PAT mode: returns user email (existing behavior).
    OAuth M2M mode: returns None - Databricks Apps proxy handles access control.
    """
    if auth.mode == AuthMode.OAUTH_M2M:
        logger.info("OAuth M2M mode: authorization delegated to Databricks Apps proxy")
        return None

    try:
        from databricks.sdk import WorkspaceClient
        if not auth.host or not auth.token:
            return None
        w = WorkspaceClient(host=auth.host, token=auth.token, auth_type="pat")
        return w.current_user.me().user_name
    except Exception as e:
        logger.warning(f"Could not determine token owner: {e}")
        return None


def get_request_user():
    """Extract user email from Databricks Apps request headers."""
    return request.headers.get("X-Forwarded-Email") or \
           request.headers.get("X-Forwarded-User") or \
           request.headers.get("X-Databricks-User-Email")


def check_authorization():
    """Check if the current user is authorized to access the app."""
    # If owner not set (local dev or SDK unavailable), allow access
    if not app_owner:
        return True, None

    current_user = get_request_user()

    # If no user identity in request (local dev), allow access
    if not current_user:
        return True, None

    # Check if current user is the owner
    if current_user != app_owner:
        logger.warning(f"Unauthorized access attempt by {current_user} (owner: {app_owner})")
        return False, current_user

    return True, None


def read_pty_output(session_id, fd):
    """Background thread to read PTY output into buffer."""
    with sessions_lock:
        pid = sessions[session_id]["pid"]

    while True:
        with sessions_lock:
            if session_id not in sessions:
                break
        try:
            readable, _, errors = select.select([fd], [], [fd], 0.5)
            if readable or errors:
                output = os.read(fd, 4096)
                if not output:
                    # EOF — process exited
                    break
                with sessions_lock:
                    if session_id in sessions:
                        sessions[session_id]["output_buffer"].append(output.decode(errors="replace"))
            else:
                # select timed out — check if process is still alive
                try:
                    pid_result, _ = os.waitpid(pid, os.WNOHANG)
                    if pid_result != 0:
                        # Process exited
                        break
                except ChildProcessError:
                    # Process already reaped
                    break
        except OSError:
            break

    # Process exited or fd closed — mark session as exited for the poll endpoint
    with sessions_lock:
        if session_id in sessions:
            sessions[session_id]["exited"] = True
            logger.info(f"Session {session_id} process exited")


def terminate_session(session_id, pid, master_fd):
    """Gracefully terminate a session: SIGHUP -> wait -> SIGKILL -> cleanup."""
    logger.info(f"Terminating stale session {session_id} (pid={pid})")
    try:
        os.kill(pid, signal.SIGHUP)
        time.sleep(GRACEFUL_SHUTDOWN_WAIT)

        # Check if still alive, force kill if needed
        try:
            os.kill(pid, 0)  # Check if process exists
            os.kill(pid, signal.SIGKILL)
            logger.info(f"Force killed session {session_id} (pid={pid})")
        except OSError:
            pass  # Already dead

        os.close(master_fd)
    except OSError:
        pass  # Process or fd already gone

    with sessions_lock:
        sessions.pop(session_id, None)


def cleanup_stale_sessions():
    """Background thread that removes sessions with no recent polling."""
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)

        now = time.time()
        stale_sessions = []

        # Find stale sessions
        with sessions_lock:
            for session_id, session in sessions.items():
                if now - session["last_poll_time"] > SESSION_TIMEOUT_SECONDS:
                    stale_sessions.append((session_id, session["pid"], session["master_fd"]))

        if stale_sessions:
            logger.info(f"Found {len(stale_sessions)} stale session(s) to clean up")

        # Terminate each stale session (outside the lock)
        for session_id, pid, master_fd in stale_sessions:
            terminate_session(session_id, pid, master_fd)


@app.before_request
def authorize_request():
    """Check authorization before processing any request."""
    # Skip auth for health check and setup status
    if request.path in ("/health", "/api/setup-status"):
        return None

    authorized, user = check_authorization()
    if not authorized:
        return jsonify({
            "error": "Unauthorized",
            "message": f"This app belongs to {app_owner}. You are logged in as {user}."
        }), 403

    return None


@app.route("/")
def index():
    with setup_lock:
        status = setup_state["status"]
    if status in ("pending", "running"):
        return send_from_directory("static", "loading.html")
    return send_from_directory("static", "index.html")


@app.route("/api/setup-status")
def get_setup_status():
    return jsonify(_get_setup_state_snapshot())


@app.route("/health")
def health():
    with sessions_lock:
        session_count = len(sessions)
    with setup_lock:
        current_setup_status = setup_state["status"]
    return jsonify({
        "status": "healthy",
        "setup_status": current_setup_status,
        "active_sessions": session_count,
        "session_timeout_seconds": SESSION_TIMEOUT_SECONDS
    })


@app.route("/api/session", methods=["POST"])
def create_session():
    """Create a new terminal session."""
    try:
        master_fd, slave_fd = pty.openpty()
        # Set up environment for the shell
        shell_env = os.environ.copy()
        shell_env["TERM"] = "xterm-256color"
        # Ensure HOME is set correctly
        if not shell_env.get("HOME") or shell_env["HOME"] == "/":
            shell_env["HOME"] = "/app/python/source_code"
        # Add ~/.local/bin to PATH for claude command
        local_bin = f"{shell_env['HOME']}/.local/bin"
        shell_env["PATH"] = f"{local_bin}:{shell_env.get('PATH', '')}"

        # Inject fresh token from TokenRefresher (OAuth M2M keeps tokens current)
        if token_refresher is not None:
            shell_env["DATABRICKS_TOKEN"] = token_refresher.current_token

        # Start shell in ~/projects/ directory
        projects_dir = os.path.join(shell_env["HOME"], "projects")
        os.makedirs(projects_dir, exist_ok=True)

        pid = subprocess.Popen(
            ["/bin/bash"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            env=shell_env,
            cwd=projects_dir
        ).pid

        session_id = str(uuid.uuid4())

        with sessions_lock:
            sessions[session_id] = {
                "master_fd": master_fd,
                "pid": pid,
                "output_buffer": deque(maxlen=1000),
                "last_poll_time": time.time(),
                "created_at": time.time()
            }

        # Start background reader thread
        thread = threading.Thread(target=read_pty_output, args=(session_id, master_fd), daemon=True)
        thread.start()

        return jsonify({"session_id": session_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/input", methods=["POST"])
def send_input():
    """Send input to the terminal."""
    data = request.json
    session_id = data.get("session_id")
    input_data = data.get("input", "")

    with sessions_lock:
        if session_id not in sessions:
            return jsonify({"error": "Session not found"}), 404

        fd = sessions[session_id]["master_fd"]

    try:
        os.write(fd, input_data.encode())
        return jsonify({"status": "ok"})
    except OSError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/output", methods=["POST"])
def get_output():
    """Get output from the terminal."""
    data = request.json
    session_id = data.get("session_id")

    with sessions_lock:
        if session_id not in sessions:
            return jsonify({"error": "Session not found"}), 404

        session = sessions[session_id]
        session["last_poll_time"] = time.time()
        buffer = session["output_buffer"]
        output = "".join(buffer)
        buffer.clear()
        exited = session.get("exited", False)

    return jsonify({"output": output, "exited": exited})


@app.route("/api/output-batch", methods=["POST"])
def get_output_batch():
    """Get output from multiple terminal sessions in one request.

    Accepts: {"session_ids": ["id1", "id2", ...]}
    Returns: {"outputs": {"id1": {"output": "...", "exited": false}, ...}}

    Unknown session_ids are silently skipped (not an error).
    """
    data = request.json or {}
    session_ids = data.get("session_ids")

    if session_ids is None:
        return jsonify({"error": "session_ids required"}), 400

    outputs = {}
    now = time.time()

    with sessions_lock:
        for sid in session_ids:
            if sid not in sessions:
                continue
            session = sessions[sid]
            session["last_poll_time"] = now
            buffer = session["output_buffer"]
            output = "".join(buffer)
            buffer.clear()
            exited = session.get("exited", False)
            outputs[sid] = {"output": output, "exited": exited}

    return jsonify({"outputs": outputs})


@app.route("/api/resize", methods=["POST"])
def resize_terminal():
    """Resize the terminal."""
    data = request.json
    session_id = data.get("session_id")
    cols = data.get("cols", 80)
    rows = data.get("rows", 24)

    with sessions_lock:
        if session_id not in sessions:
            return jsonify({"error": "Session not found"}), 404
        fd = sessions[session_id]["master_fd"]

    try:
        # Set terminal size using TIOCSWINSZ
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        return jsonify({"status": "ok"})
    except OSError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/session/close", methods=["POST"])
def close_session():
    """Gracefully close a terminal session, killing the process."""
    data = request.json
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"status": "ok", "detail": "session not found"})
        pid = session["pid"]
        master_fd = session["master_fd"]

    terminate_session(session_id, pid, master_fd)
    logger.info(f"Session {session_id} closed by client")
    return jsonify({"status": "ok"})


def initialize_app():
    """One-time init: resolve auth, detect owner, start cleanup + token refresh."""
    global app_owner, token_refresher

    # Resolve authentication (PAT or OAuth M2M)
    auth = resolve_auth()
    logger.info(f"Auth resolved: mode={auth.mode.value}, host={auth.host}")

    # Set DATABRICKS_TOKEN env var so setup scripts and subprocesses can use it
    if auth.token:
        os.environ["DATABRICKS_TOKEN"] = auth.token

    # Start token refresher (only active in OAuth M2M mode)
    token_refresher = TokenRefresher(auth)
    token_refresher.start()

    # Determine app owner
    app_owner = _get_app_owner(auth)
    if app_owner:
        logger.info(f"App owner (from token): {app_owner}")
    else:
        logger.warning("Could not determine app owner - authorization disabled")

    # Start background cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_stale_sessions, daemon=True)
    cleanup_thread.start()
    logger.info(f"Started session cleanup thread (timeout={SESSION_TIMEOUT_SECONDS}s, interval={CLEANUP_INTERVAL_SECONDS}s)")

    # Start setup in background thread — app starts immediately with loading screen
    setup_thread = threading.Thread(target=run_setup, daemon=True, name="setup-thread")
    setup_thread.start()
    logger.info("Started background setup thread")


if __name__ == "__main__":
    # Local dev only — production uses gunicorn
    initialize_app()
    port = int(os.environ.get("DATABRICKS_APP_PORT", 8000))
    app.run(host="0.0.0.0", port=port, threaded=True)
