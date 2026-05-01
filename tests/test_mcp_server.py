"""Tests for mcp_server — MCP tool layer over task_manager."""

import json
from unittest import mock

import pytest


# ── helpers ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_hooks():
    """Clear app hooks before/after each test."""
    import mcp_server

    mcp_server._app_create_session = None
    mcp_server._app_send_input = None
    mcp_server._app_close_session = None
    yield
    mcp_server._app_create_session = None
    mcp_server._app_send_input = None
    mcp_server._app_close_session = None


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path):
    """Point task_manager.SESSIONS_DIR at a temp dir."""
    sessions_dir = str(tmp_path / ".coda" / "sessions")
    with mock.patch("task_manager.SESSIONS_DIR", sessions_dir):
        yield sessions_dir


def _parse(result: str) -> dict:
    """Parse JSON string returned by MCP tools."""
    return json.loads(result)


# ── Tool registration ────────────────────────────────────────────────


class TestToolRegistration:
    def test_all_five_tools_registered(self):
        import mcp_server

        mcp = mcp_server.mcp
        # FastMCP stores tools in _tool_manager._tools dict
        tool_mgr = mcp._tool_manager
        tool_names = set(tool_mgr._tools.keys())
        expected = {
            "coda_create_session",
            "coda_run_task",
            "coda_get_status",
            "coda_get_result",
            "coda_close_session",
        }
        assert expected.issubset(tool_names), (
            f"Missing tools: {expected - tool_names}"
        )

    def test_tool_count_is_five(self):
        import mcp_server

        tool_mgr = mcp_server.mcp._tool_manager
        assert len(tool_mgr._tools) == 5


# ── coda_create_session ──────────────────────────────────────────────


class TestCodaCreateSession:
    @pytest.mark.asyncio
    async def test_creates_session_disk_only(self):
        """Without app hooks, creates disk session only."""
        import mcp_server

        result = await mcp_server.coda_create_session(
            email="a@b.com", user_id="u1", label="test"
        )
        data = _parse(result)
        assert data["status"] == "ready"
        assert data["session_id"].startswith("sess-")

    @pytest.mark.asyncio
    async def test_creates_session_with_pty_hook(self):
        """With app hooks, also creates PTY session."""
        import mcp_server

        mock_create = mock.Mock(return_value="pty-abc123")
        mcp_server.set_app_hooks(
            create_session_fn=mock_create,
            send_input_fn=mock.Mock(),
            close_session_fn=mock.Mock(),
        )

        result = await mcp_server.coda_create_session(
            email="a@b.com", user_id="u1", label="test"
        )
        data = _parse(result)
        assert data["status"] == "ready"
        mock_create.assert_called_once_with(label="hermes-mcp")

        # Verify pty_session_id was stored
        import task_manager

        session = task_manager._read_session(data["session_id"])
        assert session["pty_session_id"] == "pty-abc123"


# ── coda_run_task ────────────────────────────────────────────────────


class TestCodaRunTask:
    @pytest.mark.asyncio
    async def test_creates_task_disk_only(self):
        """Without hooks, creates disk task only."""
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]

        result = await mcp_server.coda_run_task(
            session_id=sid,
            prompt="fix the bug",
            email="a@b.com",
        )
        data = _parse(result)
        assert data["status"] == "running"
        assert data["task_id"].startswith("task-")

    @pytest.mark.asyncio
    async def test_sends_to_pty_when_hooks_set(self):
        """With hooks, sends hermes command to PTY."""
        import mcp_server
        import task_manager

        mock_send = mock.Mock()
        mcp_server.set_app_hooks(
            create_session_fn=mock.Mock(return_value="pty-xyz"),
            send_input_fn=mock_send,
            close_session_fn=mock.Mock(),
        )

        # Create session with pty_session_id
        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        task_manager._update_session_field(sid, "pty_session_id", "pty-xyz")

        with mock.patch("mcp_server.threading") as mock_threading:
            result = await mcp_server.coda_run_task(
                session_id=sid,
                prompt="fix the bug",
                email="a@b.com",
            )

        data = _parse(result)
        assert data["status"] == "running"
        # Verify send_input was called with pty session and hermes command
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "pty-xyz"  # pty_session_id
        assert "hermes" in call_args[0][1]  # command contains hermes

    @pytest.mark.asyncio
    async def test_busy_session_returns_error(self):
        """Submitting to a busy session returns error JSON."""
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        task_manager.create_task(sid, "first", "a@b.com")

        result = await mcp_server.coda_run_task(
            session_id=sid,
            prompt="second task",
            email="a@b.com",
        )
        data = _parse(result)
        assert data["status"] == "error"
        assert "already has a running task" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_yolo_permission(self):
        """permissions='yolo' produces --yolo flag."""
        import mcp_server
        import task_manager

        mock_send = mock.Mock()
        mcp_server.set_app_hooks(
            create_session_fn=mock.Mock(return_value="pty-1"),
            send_input_fn=mock_send,
            close_session_fn=mock.Mock(),
        )

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        task_manager._update_session_field(sid, "pty_session_id", "pty-1")

        with mock.patch("mcp_server.threading"):
            await mcp_server.coda_run_task(
                session_id=sid,
                prompt="go fast",
                email="a@b.com",
                permissions="yolo",
            )

        cmd = mock_send.call_args[0][1]
        assert "--yolo" in cmd


# ── coda_get_status ──────────────────────────────────────────────────


class TestCodaGetStatus:
    @pytest.mark.asyncio
    async def test_returns_running_status(self):
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        tid = task_manager.create_task(sid, "go", "a@b.com")["task_id"]

        result = await mcp_server.coda_get_status(
            task_id=tid, session_id=sid
        )
        data = _parse(result)
        assert data["task_id"] == tid
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_not_found_task(self):
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]

        result = await mcp_server.coda_get_status(
            task_id="task-nonexist", session_id=sid
        )
        data = _parse(result)
        assert data["status"] == "not_found"


# ── coda_get_result ──────────────────────────────────────────────────


class TestCodaGetResult:
    @pytest.mark.asyncio
    async def test_returns_result(self):
        import mcp_server
        import task_manager
        import os

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        tid = task_manager.create_task(sid, "go", "a@b.com")["task_id"]

        # Simulate agent writing result.json
        result_path = os.path.join(
            task_manager._task_dir(sid, tid), "result.json"
        )
        with open(result_path, "w") as f:
            json.dump(
                {
                    "summary": "Fixed the bug",
                    "files_changed": ["app.py"],
                    "artifacts": [],
                    "errors": [],
                },
                f,
            )

        result = await mcp_server.coda_get_result(
            task_id=tid, session_id=sid
        )
        data = _parse(result)
        assert data["task_id"] == tid
        assert data["summary"] == "Fixed the bug"
        assert data["files_changed"] == ["app.py"]

    @pytest.mark.asyncio
    async def test_no_result_yet(self):
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        tid = task_manager.create_task(sid, "go", "a@b.com")["task_id"]

        result = await mcp_server.coda_get_result(
            task_id=tid, session_id=sid
        )
        data = _parse(result)
        assert data["status"] == "running"
        assert "not yet available" in data["message"]


# ── coda_close_session ───────────────────────────────────────────────


class TestCodaCloseSession:
    @pytest.mark.asyncio
    async def test_closes_session_disk_only(self):
        """Without hooks, closes disk session only."""
        import mcp_server
        import task_manager

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]

        result = await mcp_server.coda_close_session(session_id=sid)
        data = _parse(result)
        assert data["session_id"] == sid
        assert data["status"] == "closed"

    @pytest.mark.asyncio
    async def test_closes_pty_when_hooks_set(self):
        """With hooks, also closes PTY session."""
        import mcp_server
        import task_manager

        mock_close = mock.Mock()
        mcp_server.set_app_hooks(
            create_session_fn=mock.Mock(),
            send_input_fn=mock.Mock(),
            close_session_fn=mock_close,
        )

        sid = task_manager.create_session("a@b.com", "u1")["session_id"]
        task_manager._update_session_field(sid, "pty_session_id", "pty-999")

        result = await mcp_server.coda_close_session(session_id=sid)
        data = _parse(result)
        assert data["status"] == "closed"
        mock_close.assert_called_once_with("pty-999")

    @pytest.mark.asyncio
    async def test_close_nonexistent_returns_error(self):
        import mcp_server

        result = await mcp_server.coda_close_session(
            session_id="sess-doesnotexist"
        )
        data = _parse(result)
        assert data["status"] == "error"
