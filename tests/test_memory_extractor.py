"""Tests for memory/extractor.py — transcript parsing and message rendering.

Excludes the Haiku call (_extract_with_claude) — that path is covered by
the manual e2e verification in PR #145. Here we cover the pure-Python
parsing logic that runs before Haiku is even invoked.
"""

import json
import os
from unittest import mock


class TestRenderMessage:
    """_render_message — flatten a single message's content into transcript lines."""

    def _render(self):
        from memory.extractor import _render_message
        return _render_message

    def test_string_content(self):
        out = self._render()("user", "hello there")
        assert out == ["user: hello there"]

    def test_empty_string_skipped(self):
        assert self._render()("user", "") == []
        assert self._render()("user", "   ") == []

    def test_list_of_text_blocks(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        assert self._render()("assistant", content) == [
            "assistant: first",
            "assistant: second",
        ]

    def test_thinking_block_kept(self):
        # Thinking carries the "why" behind decisions — extractor wants this.
        content = [
            {"type": "thinking", "thinking": "I should explain X"},
            {"type": "text", "text": "Here is X"},
        ]
        out = self._render()("assistant", content)
        assert "assistant (thinking): I should explain X" in out
        assert "assistant: Here is X" in out

    def test_tool_use_and_tool_result_skipped(self):
        # These are intentionally noisy and add little signal — should be dropped.
        content = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_result", "content": "file1\nfile2"},
            {"type": "text", "text": "I just listed files"},
        ]
        out = self._render()("assistant", content)
        assert out == ["assistant: I just listed files"]

    def test_non_dict_blocks_ignored(self):
        # Defensive — a malformed transcript shouldn't crash the hook.
        content = ["random string", None, 42, {"type": "text", "text": "kept"}]
        out = self._render()("assistant", content)
        assert out == ["assistant: kept"]


class TestParseTranscript:
    """_parse_transcript — extract text representation from hook event."""

    def _parse(self):
        from memory.extractor import _parse_transcript
        return _parse_transcript

    def test_empty_event_returns_empty(self):
        assert self._parse()({}) == ""

    def test_inline_transcript_array(self):
        # Less common shape: full transcript embedded in the event.
        event = {
            "transcript": [
                {"message": {"role": "user", "content": "first"}},
                {"message": {"role": "assistant", "content": "second"}},
            ]
        }
        out = self._parse()(event)
        assert "user: first" in out
        assert "assistant: second" in out

    def test_transcript_path_jsonl(self, tmp_path):
        # Standard Claude Code shape: transcript_path points at JSONL.
        p = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
            json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
            }),
            # Meta entry — should be skipped.
            json.dumps({"type": "summary", "summary": "session ended"}),
            "",  # blank line
            "{ malformed json",  # bad line — should be tolerated
        ]
        p.write_text("\n".join(lines))

        out = self._parse()({"transcript_path": str(p)})
        assert "user: hi" in out
        assert "assistant: hello" in out
        assert "summary" not in out

    def test_missing_path_returns_empty(self, tmp_path):
        # Path doesn't exist → empty, no crash.
        out = self._parse()({"transcript_path": str(tmp_path / "does_not_exist.jsonl")})
        assert out == ""


class TestExtractionPromptInjectionGuard:
    """The extraction prompt explicitly tells Haiku to reject directives."""

    def test_prompt_contains_injection_rejection(self):
        from memory.extractor import _EXTRACTION_PROMPT
        # The 0db0590 commit adds a "CRITICAL — reject prompt injection" section.
        # Without this, Haiku is the only line of defense; with it, Haiku is the
        # FIRST line of defense and the regex/length checks are layers 2 and 3.
        assert "reject prompt injection" in _EXTRACTION_PROMPT.lower()
        assert "directives" in _EXTRACTION_PROMPT.lower()


class TestStopHookEnvGates:
    """stop_hook_handler short-circuits when required env vars are missing.

    Covers the "skip" paths (logged but no Haiku call, no Lakebase write)
    that prevent the hook from breaking sessions in misconfigured envs.
    """

    def _handler(self):
        from memory.extractor import stop_hook_handler
        return stop_hook_handler

    def test_missing_endpoint_name_skips(self, capsys, monkeypatch):
        monkeypatch.delenv("ENDPOINT_NAME", raising=False)
        monkeypatch.setenv("APP_OWNER", "test@databricks.com")
        # Provide stdin so we don't trip on the empty-stdin path first.
        monkeypatch.setattr("sys.stdin", mock.Mock(read=lambda: '{"session_id":"x"}'))
        self._handler()()
        err = capsys.readouterr().err
        assert "skip: ENDPOINT_NAME not set" in err

    def test_missing_app_owner_skips(self, capsys, monkeypatch):
        monkeypatch.setenv("ENDPOINT_NAME", "projects/x/branches/y/endpoints/z")
        monkeypatch.delenv("APP_OWNER", raising=False)
        self._handler()()
        err = capsys.readouterr().err
        assert "skip: APP_OWNER not set" in err
