"""Tests for mcp_server — v2 background execution + inbox API."""

import json
import os
from unittest import mock

import pytest


# ── helpers ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_hooks():
    """Clear app hooks before/after each test."""
    from coda_mcp import mcp_server

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
    with mock.patch("coda_mcp.task_manager.SESSIONS_DIR", sessions_dir):
        yield sessions_dir


def _parse(result: str) -> dict:
    """Parse JSON string returned by MCP tools."""
    return json.loads(result)


# ── Tool registration ────────────────────────────────────────────────


class TestToolRegistration:
    def test_three_tools_registered(self):
        from coda_mcp import mcp_server

        tool_mgr = mcp_server.mcp._tool_manager
        tool_names = set(tool_mgr._tools.keys())
        expected = {"coda_run", "coda_inbox", "coda_get_result"}
        assert expected == tool_names, f"Expected {expected}, got {tool_names}"

    def test_tool_count_is_three(self):
        from coda_mcp import mcp_server

        tool_mgr = mcp_server.mcp._tool_manager
        assert len(tool_mgr._tools) == 3


# ── coda_run ─────────────────────────────────────────────────────────


class TestCodaRun:
    @pytest.mark.asyncio
    async def test_creates_task_disk_only(self):
        """Without app hooks, creates session+task on disk, returns immediately."""
        from coda_mcp import mcp_server

        result = await mcp_server.coda_run(
            prompt="fix the bug",
            email="a@b.com",
        )
        data = _parse(result)
        assert data["status"] == "running"
        assert data["task_id"].startswith("task-")
        assert data["session_id"].startswith("sess-")

    @pytest.mark.asyncio
    async def test_auto_creates_session(self):
        """coda_run auto-creates a session — no separate create_session needed."""
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        result = await mcp_server.coda_run(
            prompt="build pipeline",
            email="a@b.com",
        )
        data = _parse(result)
        session = task_manager._read_session(data["session_id"])
        assert session["email"] == "a@b.com"
        assert session["status"] == "busy"  # task is running

    @pytest.mark.asyncio
    async def test_sends_to_pty_when_hooks_set(self):
        """With hooks, creates PTY and sends hermes command."""
        from coda_mcp import mcp_server

        mock_create = mock.Mock(return_value="pty-xyz")
        mock_send = mock.Mock()
        mcp_server.set_app_hooks(
            create_session_fn=mock_create,
            send_input_fn=mock_send,
            close_session_fn=mock.Mock(),
        )

        with mock.patch("coda_mcp.mcp_server.threading"):
            result = await mcp_server.coda_run(
                prompt="fix the bug",
                email="a@b.com",
            )

        data = _parse(result)
        assert data["status"] == "running"
        mock_create.assert_called_once_with(label="hermes-mcp")
        mock_send.assert_called_once()
        assert "hermes" in mock_send.call_args[0][1]

    @pytest.mark.asyncio
    async def test_yolo_permission(self):
        """permissions='yolo' produces --yolo flag in PTY command."""
        from coda_mcp import mcp_server

        mock_send = mock.Mock()
        mcp_server.set_app_hooks(
            create_session_fn=mock.Mock(return_value="pty-1"),
            send_input_fn=mock_send,
            close_session_fn=mock.Mock(),
        )

        with mock.patch("coda_mcp.mcp_server.threading"):
            await mcp_server.coda_run(
                prompt="go fast",
                email="a@b.com",
                permissions="yolo",
            )

        cmd = mock_send.call_args[0][1]
        assert "--yolo" in cmd

    @pytest.mark.asyncio
    async def test_previous_session_id_in_prompt(self):
        """previous_session_id appears in the wrapped prompt."""
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        # Create a "prior" session with a completed task
        prior = task_manager.create_session("a@b.com", "u1")
        prior_sid = prior["session_id"]

        result = await mcp_server.coda_run(
            prompt="add tests",
            email="a@b.com",
            previous_session_id=prior_sid,
        )
        data = _parse(result)

        # Read the prompt.txt and verify prior session reference
        tdir = task_manager._task_dir(data["session_id"], data["task_id"])
        with open(os.path.join(tdir, "prompt.txt")) as f:
            prompt_text = f.read()

        assert f"PRIOR SESSION: {prior_sid}" in prompt_text

    @pytest.mark.asyncio
    async def test_meta_json_written(self):
        """coda_run writes meta.json with task metadata."""
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        result = await mcp_server.coda_run(
            prompt="build a dashboard for sales",
            email="alice@test.com",
            previous_session_id="sess-old",
        )
        data = _parse(result)

        meta_path = os.path.join(
            task_manager._task_dir(data["session_id"], data["task_id"]),
            "meta.json",
        )
        with open(meta_path) as f:
            meta = json.load(f)

        assert meta["email"] == "alice@test.com"
        assert meta["previous_session_id"] == "sess-old"
        assert meta["prompt_summary"] == "build a dashboard for sales"
        assert "created_at" in meta

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Exceeding MAX_CONCURRENT_TASKS returns an error."""
        from coda_mcp import mcp_server

        with mock.patch("coda_mcp.task_manager.MAX_CONCURRENT_TASKS", 1):
            # First task succeeds
            r1 = await mcp_server.coda_run(prompt="task1", email="a@b.com")
            assert _parse(r1)["status"] == "running"

            # Second task should fail (1 already running)
            r2 = await mcp_server.coda_run(prompt="task2", email="a@b.com")
            d2 = _parse(r2)
            assert d2["status"] == "error"
            assert "concurrency" in d2["error"].lower()


# ── coda_inbox ───────────────────────────────────────────────────────


class TestCodaInbox:
    @pytest.mark.asyncio
    async def test_empty_inbox(self):
        """No tasks → empty inbox."""
        from coda_mcp import mcp_server

        result = await mcp_server.coda_inbox()
        data = _parse(result)
        assert data["tasks"] == []
        assert data["counts"] == {"running": 0, "completed": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_running_task_in_inbox(self):
        """A running task shows up in the inbox."""
        from coda_mcp import mcp_server

        await mcp_server.coda_run(prompt="build pipeline", email="a@b.com")

        result = await mcp_server.coda_inbox()
        data = _parse(result)
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "running"
        assert data["tasks"][0]["prompt_summary"] == "build pipeline"
        assert data["counts"]["running"] == 1

    @pytest.mark.asyncio
    async def test_completed_task_in_inbox(self):
        """A completed task shows summary in inbox."""
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        r = await mcp_server.coda_run(prompt="fix bug", email="a@b.com")
        d = _parse(r)

        # Simulate agent writing result.json
        tdir = task_manager._task_dir(d["session_id"], d["task_id"])
        result_path = os.path.join(tdir, "result.json")
        with open(result_path, "w") as f:
            json.dump({
                "status": "completed",
                "summary": "Fixed the login bug",
                "files_changed": ["auth.py"],
                "artifacts": [],
                "errors": [],
            }, f)

        result = await mcp_server.coda_inbox()
        data = _parse(result)
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "completed"
        assert data["tasks"][0]["summary"] == "Fixed the login bug"

    @pytest.mark.asyncio
    async def test_status_filter(self):
        """Filtering inbox by status works."""
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        # Create two tasks — one running, one completed
        r1 = await mcp_server.coda_run(prompt="task1", email="a@b.com")
        d1 = _parse(r1)

        r2 = await mcp_server.coda_run(prompt="task2", email="a@b.com")
        d2 = _parse(r2)

        # Complete task2
        tdir = task_manager._task_dir(d2["session_id"], d2["task_id"])
        with open(os.path.join(tdir, "result.json"), "w") as f:
            json.dump({"status": "completed", "summary": "done"}, f)

        # Filter running only
        result = await mcp_server.coda_inbox(status="running")
        data = _parse(result)
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == d1["task_id"]

    @pytest.mark.asyncio
    async def test_multiple_tasks_sorted_recent_first(self):
        """Inbox returns tasks sorted most recent first."""
        from coda_mcp import mcp_server

        r1 = await mcp_server.coda_run(prompt="first", email="a@b.com")
        r2 = await mcp_server.coda_run(prompt="second", email="a@b.com")

        result = await mcp_server.coda_inbox()
        data = _parse(result)
        assert len(data["tasks"]) == 2
        # Most recent first
        assert data["tasks"][0]["prompt_summary"] == "second"
        assert data["tasks"][1]["prompt_summary"] == "first"


# ── coda_get_result ──────────────────────────────────────────────────


class TestCodaGetResult:
    @pytest.mark.asyncio
    async def test_returns_result(self):
        from coda_mcp import mcp_server
        from coda_mcp import task_manager

        r = await mcp_server.coda_run(prompt="go", email="a@b.com")
        d = _parse(r)

        # Simulate agent writing result.json
        tdir = task_manager._task_dir(d["session_id"], d["task_id"])
        with open(os.path.join(tdir, "result.json"), "w") as f:
            json.dump({
                "summary": "Fixed the bug",
                "files_changed": ["app.py"],
                "artifacts": [],
                "errors": [],
            }, f)

        result = await mcp_server.coda_get_result(
            task_id=d["task_id"], session_id=d["session_id"]
        )
        data = _parse(result)
        assert data["task_id"] == d["task_id"]
        assert data["session_id"] == d["session_id"]
        assert data["summary"] == "Fixed the bug"

    @pytest.mark.asyncio
    async def test_no_result_yet(self):
        from coda_mcp import mcp_server

        r = await mcp_server.coda_run(prompt="go", email="a@b.com")
        d = _parse(r)

        result = await mcp_server.coda_get_result(
            task_id=d["task_id"], session_id=d["session_id"]
        )
        data = _parse(result)
        assert data["status"] == "running"
        assert "not yet available" in data["message"]
