"""End-to-end MCP integration tests.

Exercises the full flow: create session -> run task -> check status ->
get result -> close session.  No real PTY — app hooks are mocked.
"""

import json
import os
import time
from unittest.mock import MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _parse(result: str) -> dict:
    """Parse JSON string returned by MCP tools."""
    return json.loads(result)


# ── fixture ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_env(tmp_path):
    """Redirect state to tmp and mock PTY hooks."""
    import task_manager as tm
    import mcp_server as ms

    original_dir = tm.SESSIONS_DIR
    tm.SESSIONS_DIR = str(tmp_path / "sessions")

    mock_send = MagicMock()
    mock_close = MagicMock()
    ms.set_app_hooks(
        create_session_fn=lambda label: f"pty-mock-{label}",
        send_input_fn=mock_send,
        close_session_fn=mock_close,
    )

    yield {"tmp": tmp_path, "mock_send": mock_send, "mock_close": mock_close}

    tm.SESSIONS_DIR = original_dir
    ms.set_app_hooks(None, None, None)


# ── 1. Happy-path end-to-end ─────────────────────────────────────────


class TestFullMcpFlow:
    @pytest.mark.asyncio
    async def test_full_mcp_flow(self, isolated_env):
        """Happy path: create -> run -> status -> result -> close."""
        import mcp_server as ms
        import task_manager as tm

        # Step 1: create session
        raw = await ms.coda_create_session(email="alice@test.com")
        session = _parse(raw)
        assert session["status"] == "ready"
        session_id = session["session_id"]
        assert session_id.startswith("sess-")

        # Step 2: run task
        raw = await ms.coda_run_task(
            session_id=session_id,
            prompt="create a sales pipeline",
            email="alice@test.com",
            context='{"tables": ["sales.transactions"]}',
        )
        task = _parse(raw)
        assert task["status"] == "running"
        task_id = task["task_id"]
        assert task_id.startswith("task-")

        # Step 3: status shows running, no extra progress yet
        raw = await ms.coda_get_status(task_id=task_id, session_id=session_id)
        status = _parse(raw)
        assert status["status"] == "running"
        assert status["task_id"] == task_id

        # Step 4: simulate agent writing a progress line to status.jsonl
        status_path = os.path.join(
            tm._task_dir(session_id, task_id), "status.jsonl"
        )
        with open(status_path, "a") as f:
            f.write(
                json.dumps(
                    {"status": "progress", "step": "built model", "ts": time.time()}
                )
                + "\n"
            )

        raw = await ms.coda_get_status(task_id=task_id, session_id=session_id)
        status = _parse(raw)
        assert status["status"] == "progress"
        assert status["step"] == "built model"

        # Step 5: simulate agent writing result.json
        result_path = os.path.join(
            tm._task_dir(session_id, task_id), "result.json"
        )
        with open(result_path, "w") as f:
            json.dump(
                {
                    "summary": "Created sales pipeline with 3 stages",
                    "files_changed": ["pipeline.py", "config.yaml"],
                    "artifacts": ["/workspace/pipeline.py"],
                    "errors": [],
                },
                f,
            )

        # Step 6: mark task complete
        tm.complete_task(session_id, task_id)

        # Step 7: retrieve result via MCP tool
        raw = await ms.coda_get_result(task_id=task_id, session_id=session_id)
        result = _parse(raw)
        assert result["task_id"] == task_id
        assert result["status"] == "done"
        assert result["summary"] == "Created sales pipeline with 3 stages"
        assert result["files_changed"] == ["pipeline.py", "config.yaml"]
        assert result["artifacts"] == ["/workspace/pipeline.py"]
        assert result["errors"] == []

        # Step 8: close session
        raw = await ms.coda_close_session(session_id=session_id)
        closed = _parse(raw)
        assert closed["session_id"] == session_id
        assert closed["status"] == "closed"


# ── 2. Busy session rejects second task ──────────────────────────────


class TestBusySessionRejectsSecondTask:
    @pytest.mark.asyncio
    async def test_busy_session_rejects_second_task(self, isolated_env):
        """A session with a running task must reject a second submission."""
        import mcp_server as ms

        raw = await ms.coda_create_session(email="bob@test.com")
        session_id = _parse(raw)["session_id"]

        # First task succeeds
        raw = await ms.coda_run_task(
            session_id=session_id,
            prompt="first task",
            email="bob@test.com",
        )
        first = _parse(raw)
        assert first["status"] == "running"

        # Second task must fail with "busy"
        raw = await ms.coda_run_task(
            session_id=session_id,
            prompt="second task",
            email="bob@test.com",
        )
        second = _parse(raw)
        assert second["status"] == "error"
        assert "busy" in second["error"].lower() or "already has a running task" in second["error"].lower()


# ── 3. context_hint written to prompt.txt ────────────────────────────


class TestContextHintNewTopic:
    @pytest.mark.asyncio
    async def test_context_hint_new_topic(self, isolated_env):
        """context_hint='new_topic' appears in the prompt.txt envelope."""
        import mcp_server as ms
        import task_manager as tm

        raw = await ms.coda_create_session(email="carol@test.com")
        session_id = _parse(raw)["session_id"]

        raw = await ms.coda_run_task(
            session_id=session_id,
            prompt="start fresh analysis",
            email="carol@test.com",
            context_hint="new_topic",
        )
        task_id = _parse(raw)["task_id"]

        prompt_path = os.path.join(
            tm._task_dir(session_id, task_id), "prompt.txt"
        )
        with open(prompt_path) as f:
            prompt_text = f.read()

        assert "context_hint: new_topic" in prompt_text


# ── 4. Yolo permissions → --yolo flag ───────────────────────────────


class TestYoloPermissions:
    @pytest.mark.asyncio
    async def test_yolo_permissions(self, isolated_env):
        """permissions='yolo' causes the PTY command to include --yolo."""
        import mcp_server as ms

        mock_send = isolated_env["mock_send"]

        raw = await ms.coda_create_session(email="dave@test.com")
        session_id = _parse(raw)["session_id"]

        await ms.coda_run_task(
            session_id=session_id,
            prompt="deploy everything",
            email="dave@test.com",
            permissions="yolo",
        )

        mock_send.assert_called_once()
        cmd = mock_send.call_args[0][1]
        assert "--yolo" in cmd


# ── 5. Close nonexistent session → error ─────────────────────────────


class TestCloseNonexistentSession:
    @pytest.mark.asyncio
    async def test_close_nonexistent_session(self, isolated_env):
        """Closing a session that was never created returns an error."""
        import mcp_server as ms

        raw = await ms.coda_close_session(session_id="sess-doesnotexist999")
        data = _parse(raw)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower() or "does not exist" in data["error"].lower()
