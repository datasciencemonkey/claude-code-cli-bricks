"""Flask-native MCP JSON-RPC endpoint.

Implements the MCP protocol as a plain Flask route — no ASGI bridge needed.
This keeps gunicorn + Flask-SocketIO working for WebSocket terminal I/O
while serving MCP over standard HTTP.
"""
import asyncio
import json
import logging
import os

from flask import Blueprint, request, jsonify
from utils import ensure_https

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

# Import tool functions from mcp_server.py
from mcp_server import (
    mcp as mcp_instance,
    coda_create_session,
    coda_run_task,
    coda_get_status,
    coda_get_result,
    coda_close_session,
)

# Tool function dispatch
_TOOL_DISPATCH = {
    "coda_create_session": coda_create_session,
    "coda_run_task": coda_run_task,
    "coda_get_status": coda_get_status,
    "coda_get_result": coda_get_result,
    "coda_close_session": coda_close_session,
}

SERVER_INFO = {
    "name": "coda",
    "version": "1.0.0",
}

CAPABILITIES = {
    "tools": {"listChanged": False},
}


def _check_origin():
    """Validate Origin header against workspace URL."""
    origin = request.headers.get("Origin", "")
    if not origin:
        return True  # No origin = same-origin or non-browser
    databricks_host = os.environ.get("DATABRICKS_HOST", "")
    if not databricks_host:
        return True  # No host configured = allow all
    allowed = ensure_https(databricks_host).rstrip("/")
    return origin.rstrip("/") == allowed


def _cors_headers():
    """Build CORS response headers."""
    headers = {}
    origin = request.headers.get("Origin", "")
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Accept, Mcp-Session-Id"
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers


@mcp_bp.route("/mcp", methods=["POST", "OPTIONS", "GET"])
def mcp_handler():
    # Handle CORS preflight
    if request.method == "OPTIONS":
        resp = jsonify({})
        resp.status_code = 204
        for k, v in _cors_headers().items():
            resp.headers[k] = v
        return resp

    # Handle GET for SSE (not supported in stateless mode)
    if request.method == "GET":
        resp = jsonify({"error": "SSE not supported. Use POST."})
        resp.status_code = 405
        return resp

    # Validate origin
    if not _check_origin():
        return jsonify({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid origin"}
        }), 403

    data = request.get_json(silent=True) or {}
    method = data.get("method", "")
    req_id = data.get("id")
    params = data.get("params", {})

    # Route by method
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": mcp_instance._instructions if hasattr(mcp_instance, '_instructions') else "",
        }
        resp = jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})

    elif method == "notifications/initialized":
        # No-op acknowledgment — return empty OK
        resp = jsonify({})
        resp.status_code = 200

    elif method == "tools/list":
        tools = _build_tools_list()
        resp = jsonify({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        tool_fn = _TOOL_DISPATCH.get(tool_name)
        if not tool_fn:
            resp = jsonify({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            })
        else:
            try:
                # Tool functions are async — run them
                result_str = asyncio.run(tool_fn(**arguments))
                result_data = json.loads(result_str)
                resp = jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result_str}],
                        "isError": "error" in result_data,
                    }
                })
            except Exception as e:
                resp = jsonify({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": str(e)}
                })

    elif method == "ping":
        resp = jsonify({"jsonrpc": "2.0", "id": req_id, "result": {}})

    else:
        resp = jsonify({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        })

    # Add CORS headers
    for k, v in _cors_headers().items():
        resp.headers[k] = v

    return resp


def _build_tools_list():
    """Extract tool definitions from FastMCP registry."""
    tools = []
    # Access FastMCP's internal tool manager
    tool_manager = mcp_instance._tool_manager
    for name, tool in tool_manager._tools.items():
        tool_dict = {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.parameters if hasattr(tool, 'parameters') else {},
        }
        if hasattr(tool, 'annotations') and tool.annotations:
            tool_dict["annotations"] = {}
            if tool.annotations.readOnlyHint is not None:
                tool_dict["annotations"]["readOnlyHint"] = tool.annotations.readOnlyHint
            if tool.annotations.destructiveHint is not None:
                tool_dict["annotations"]["destructiveHint"] = tool.annotations.destructiveHint
            if tool.annotations.idempotentHint is not None:
                tool_dict["annotations"]["idempotentHint"] = tool.annotations.idempotentHint
        tools.append(tool_dict)
    return tools
