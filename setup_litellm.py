#!/usr/bin/env python
"""Start a lightweight local proxy to sanitize empty content blocks before they reach Databricks.

OpenCode occasionally produces empty text content blocks in messages, which the Databricks
Foundation Model API rejects with: "messages: text content blocks must be non-empty"
(see https://github.com/sst/opencode/issues/5028).

This proxy strips empty/whitespace-only text blocks before forwarding requests to the
Databricks AI Gateway. Runs on localhost:4000 (internal only, never exposed externally).

No external dependencies — uses stdlib + requests (already installed via databricks-sdk).
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from utils import ensure_https

PROXY_PORT = 4000
PROXY_HOST = "127.0.0.1"
HEALTH_TIMEOUT = 15  # seconds to wait for proxy to be ready
HEALTH_POLL_INTERVAL = 0.5

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Databricks configuration
gateway_host = ensure_https(os.environ.get("DATABRICKS_GATEWAY_HOST", "").rstrip("/"))
host = ensure_https(os.environ.get("DATABRICKS_HOST", "").rstrip("/"))
token = os.environ.get("DATABRICKS_TOKEN", "")

if not token:
    print("Warning: DATABRICKS_TOKEN not set, skipping proxy setup")
    sys.exit(0)

# Determine the upstream base URL
if gateway_host:
    upstream_base = f"{gateway_host}/mlflow/v1"
    print(f"Content-filter proxy will forward to AI Gateway: {gateway_host}")
else:
    upstream_base = f"{host}/serving-endpoints"
    print(f"Content-filter proxy will forward to: {host}/serving-endpoints")

# Write the proxy server script
proxy_script = home / ".content-filter-proxy.py"
proxy_script.write_text(f'''#!/usr/bin/env python
"""Minimal HTTP proxy that strips empty text content blocks from OpenAI-compatible API requests."""
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

UPSTREAM_BASE = "{upstream_base}"
LISTEN_HOST = "{PROXY_HOST}"
LISTEN_PORT = {PROXY_PORT}


def sanitize_messages(messages):
    """Strip empty/whitespace-only text content blocks from messages."""
    if not isinstance(messages, list):
        return messages
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            filtered = [
                block for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and block.get("text", "").strip() == ""
                )
            ]
            # If all content blocks were empty, keep the message but with empty list
            # (let the API decide how to handle it)
            msg = {{**msg, "content": filtered if filtered else content[:0]}}
        elif isinstance(content, str) and content.strip() == "":
            # Skip messages with empty string content
            continue
        cleaned.append(msg)
    return cleaned


class ProxyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Parse and sanitize
        try:
            data = json.loads(body)
            if "messages" in data:
                data["messages"] = sanitize_messages(data["messages"])
            body = json.dumps(data).encode()
        except (json.JSONDecodeError, KeyError):
            pass  # Forward as-is if not JSON

        # Build upstream URL
        upstream_url = UPSTREAM_BASE + self.path

        # Forward headers (pass through auth, content-type, etc.)
        headers = {{}}
        for key in self.headers:
            if key.lower() not in ("host", "content-length", "transfer-encoding"):
                headers[key] = self.headers[key]
        headers["Content-Length"] = str(len(body))

        # Check if client wants streaming
        is_stream = False
        try:
            is_stream = json.loads(body).get("stream", False)
        except Exception:
            pass

        try:
            resp = requests.post(
                upstream_url,
                data=body,
                headers=headers,
                stream=is_stream,
                timeout=300,
            )

            # Send response status and headers
            self.send_response(resp.status_code)
            for key, value in resp.headers.items():
                if key.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                    self.send_header(key, value)
            self.end_headers()

            # Stream or send response body
            if is_stream:
                for chunk in resp.iter_content(chunk_size=1024):
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()
            else:
                self.wfile.write(resp.content)

        except requests.exceptions.ConnectionError as e:
            self.send_error(502, f"Upstream connection failed: {{e}}")
        except requests.exceptions.Timeout:
            self.send_error(504, "Upstream timeout")

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({{"status": "ok", "upstream": UPSTREAM_BASE}}).encode())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        """Suppress request logging to keep container logs clean."""
        pass


if __name__ == "__main__":
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(f"Content-filter proxy listening on {{LISTEN_HOST}}:{{LISTEN_PORT}}")
    print(f"Forwarding to: {{UPSTREAM_BASE}}")
    sys.stdout.flush()
    server.serve_forever()
''')

print(f"Proxy script written to {proxy_script}")

# Start proxy as a background process
log_path = home / ".content-filter-proxy.log"
print(f"Starting content-filter proxy on {PROXY_HOST}:{PROXY_PORT}...")

proc = subprocess.Popen(
    [sys.executable, str(proxy_script)],
    stdout=open(log_path, "w"),
    stderr=subprocess.STDOUT,
    env=os.environ.copy(),
    start_new_session=True,  # Detach from parent process group
)

# Write PID file for cleanup
pid_path = home / ".content-filter-proxy.pid"
pid_path.write_text(str(proc.pid))
print(f"Proxy started (PID: {proc.pid})")

# Wait for health check
health_url = f"http://{PROXY_HOST}:{PROXY_PORT}/health"
start = time.time()
ready = False

while time.time() - start < HEALTH_TIMEOUT:
    try:
        resp = urlopen(Request(health_url), timeout=2)
        if resp.status == 200:
            ready = True
            break
    except (URLError, OSError):
        pass

    # Check if process died
    if proc.poll() is not None:
        print(f"Error: Proxy exited with code {proc.returncode}")
        try:
            print(f"Logs: {log_path.read_text()[:500]}")
        except Exception:
            pass
        sys.exit(1)

    time.sleep(HEALTH_POLL_INTERVAL)

if ready:
    elapsed = time.time() - start
    print(f"Content-filter proxy ready on {PROXY_HOST}:{PROXY_PORT} ({elapsed:.1f}s)")
else:
    print(f"Warning: Proxy health check timed out after {HEALTH_TIMEOUT}s")
    try:
        print(f"Logs: {log_path.read_text()[:500]}")
    except Exception:
        pass
