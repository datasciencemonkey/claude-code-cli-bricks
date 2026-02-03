import os
import pty
import select
import subprocess
from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, request

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

# Store PTY file descriptors per session
sessions = {}


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@socketio.on("connect")
def handle_connect():
    """Spawn a new PTY bash shell for this connection."""
    try:
        master_fd, slave_fd = pty.openpty()
        pid = subprocess.Popen(
            ["/bin/bash"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid
        ).pid
        sessions[request.sid] = {"master_fd": master_fd, "pid": pid}
        socketio.start_background_task(read_pty_output, request.sid, master_fd)
    except Exception as e:
        emit("output", f"\x1b[31mError spawning shell: {e}\x1b[0m\r\n")


@socketio.on("input")
def handle_input(data):
    """Forward user input to the PTY."""
    fd = sessions.get(request.sid, {}).get("master_fd")
    if fd:
        os.write(fd, data.encode())


@socketio.on("disconnect")
def handle_disconnect():
    """Clean up PTY on disconnect."""
    session = sessions.pop(request.sid, None)
    if session:
        os.close(session["master_fd"])


def read_pty_output(sid, fd):
    """Read PTY output and send to browser."""
    while sid in sessions:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                output = os.read(fd, 1024).decode(errors="replace")
                socketio.emit("output", output, to=sid)
            except OSError:
                socketio.emit("output", "\r\n\x1b[31mShell disconnected.\x1b[0m\r\n", to=sid)
                break


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)
