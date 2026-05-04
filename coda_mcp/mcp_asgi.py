"""Native MCP ASGI app following Databricks Genie Code requirements exactly.

Per docs: https://docs.databricks.com/aws/en/genie-code/mcp
- MCP server at /mcp
- stateless_http=True
- CORSMiddleware with workspace origin

Also mounts Flask at all other paths via WSGIMiddleware for the terminal UI.
WebSocket will fall back to HTTP polling under ASGI — this is expected and works.

Usage in app.yaml::

    command: ["uvicorn", "mcp_asgi:app", "--host", "0.0.0.0", "--port", "8000"]
"""

import os
import logging
import warnings

from starlette.middleware.cors import CORSMiddleware

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from starlette.middleware.wsgi import WSGIMiddleware

from coda_mcp.mcp_server import mcp as mcp_instance, set_app_hooks
from utils import ensure_https

logger = logging.getLogger(__name__)

# ── Build allowed origins from DATABRICKS_HOST ─────────────────────
_databricks_host = os.environ.get("DATABRICKS_HOST", "")
ALLOWED_ORIGINS = []
if _databricks_host:
    ALLOWED_ORIGINS.append(ensure_https(_databricks_host).rstrip("/"))

# ── Import and initialize Flask app ────────────────────────────────
from app import (
    app as flask_app,
    initialize_app,
    mcp_create_pty_session,
    mcp_send_input,
    mcp_close_pty_session,
)

initialize_app()

# Wire MCP tools to PTY infrastructure
set_app_hooks(
    create_session_fn=mcp_create_pty_session,
    send_input_fn=mcp_send_input,
    close_session_fn=mcp_close_pty_session,
)

# ── Build the ASGI app per Genie Code docs ─────────────────────────
# "mcp_app = mcp_server.http_app(stateless_http=True)"
# stateless_http and json_response are already set on the FastMCP instance
mcp_starlette = mcp_instance.streamable_http_app()

# Mount Flask as catch-all via WSGI adapter
flask_asgi = WSGIMiddleware(flask_app.wsgi_app)
mcp_starlette.mount("/", app=flask_asgi)

# "app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, ...)"
mcp_starlette.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app = mcp_starlette
