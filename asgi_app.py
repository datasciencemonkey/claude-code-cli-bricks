"""ASGI application that serves both Flask (WSGI) and MCP (ASGI) on one port.

Genie Code requires the MCP endpoint at /mcp as a native Starlette/ASGI app
with ``stateless_http=True``.  Flask is mounted at all other paths via
Starlette's WSGIMiddleware adapter.

The MCP ``streamable_http_app()`` returns a Starlette app with a route at
``/mcp`` and its own lifespan manager.  We add Flask as a catch-all mount
to that same Starlette app so everything runs under one process and one port.

Usage in app.yaml::

    command: ["uvicorn", "asgi_app:application", "--host", "0.0.0.0", "--port", "8000"]
"""

import os
import logging
import warnings

from starlette.middleware.cors import CORSMiddleware

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from starlette.middleware.wsgi import WSGIMiddleware

logger = logging.getLogger(__name__)


def create_asgi_app():
    """Build the combined ASGI application."""
    from app import app as flask_app
    from mcp_server import mcp as mcp_instance, set_app_hooks
    from app import mcp_create_pty_session, mcp_send_input, mcp_close_pty_session
    from utils import ensure_https

    # Wire MCP tools to PTY infrastructure
    set_app_hooks(
        create_session_fn=mcp_create_pty_session,
        send_input_fn=mcp_send_input,
        close_session_fn=mcp_close_pty_session,
    )

    # Start from the MCP Starlette app — it owns the /mcp route and lifespan
    app = mcp_instance.streamable_http_app()

    # Mount Flask at root as catch-all (must come after /mcp route)
    flask_asgi = WSGIMiddleware(flask_app.wsgi_app)
    app.mount("/", app=flask_asgi)

    # CORS for Genie Code cross-origin requests
    databricks_host = os.environ.get("DATABRICKS_HOST", "")
    allowed_origins = []
    if databricks_host:
        allowed_origins.append(ensure_https(databricks_host))

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    return app


application = create_asgi_app()
