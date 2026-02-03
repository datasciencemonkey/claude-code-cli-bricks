import os
import pty
import fcntl
import struct
import termios
import select
import subprocess
import uuid
import threading
from flask import Flask, send_from_directory, request, jsonify, session
from collections import deque

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.urandom(24)

# Store sessions: {session_id: {"master_fd": fd, "pid": pid, "output_buffer": deque}}
sessions = {}
sessions_lock = threading.Lock()


def read_pty_output(session_id, fd):
    """Background thread to read PTY output into buffer."""
    while True:
        with sessions_lock:
            if session_id not in sessions:
                break
        try:
            if select.select([fd], [], [], 0.1)[0]:
                output = os.read(fd, 4096).decode(errors="replace")
                with sessions_lock:
                    if session_id in sessions:
                        sessions[session_id]["output_buffer"].append(output)
        except OSError:
            break


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "sessions": len(sessions)})


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

        pid = subprocess.Popen(
            ["/bin/bash"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            env=shell_env
        ).pid

        session_id = str(uuid.uuid4())

        with sessions_lock:
            sessions[session_id] = {
                "master_fd": master_fd,
                "pid": pid,
                "output_buffer": deque(maxlen=1000)
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

        buffer = sessions[session_id]["output_buffer"]
        output = "".join(buffer)
        buffer.clear()

    return jsonify({"output": output})


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


@app.route("/api/session", methods=["DELETE"])
def delete_session():
    """Close a terminal session."""
    data = request.json
    session_id = data.get("session_id")

    with sessions_lock:
        if session_id in sessions:
            try:
                os.close(sessions[session_id]["master_fd"])
            except:
                pass
            del sessions[session_id]

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
