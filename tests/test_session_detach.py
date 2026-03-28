"""Tests for _get_session_process() helper.

Verifies that the helper correctly detects the foreground child process
of a shell session, falls back to the shell itself, and returns "unknown"
for dead or invalid PIDs.
"""

import subprocess
import sys
import time
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers — import app with initialize_app mocked out
# ---------------------------------------------------------------------------

def _get_app():
    """Import app with initialize_app mocked out."""
    with mock.patch("app.initialize_app"):
        import app as app_module
        app_module.app.config["TESTING"] = True
        return app_module


# ---------------------------------------------------------------------------
# Tests for _get_session_process
# ---------------------------------------------------------------------------


class TestGetSessionProcess:
    """Tests for _get_session_process() helper."""

    def test_detects_child_process_name(self):
        """When a shell has a child process, return the child's name."""
        app_mod = _get_app()

        # Launch a shell (bash) with a child process (sleep)
        shell = subprocess.Popen(
            ["bash", "-c", "sleep 300"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Give the child time to spawn
        time.sleep(0.5)

        try:
            result = app_mod._get_session_process(shell.pid)
            assert result == "sleep", f"Expected 'sleep', got '{result}'"
        finally:
            shell.kill()
            shell.wait()

    def test_returns_parent_process_name_when_no_children(self):
        """When a shell has no foreground children, return the shell name."""
        app_mod = _get_app()

        # Launch a bare shell that just sleeps via bash built-in wait
        # Use cat which will block on stdin with no children of its own
        proc = subprocess.Popen(
            ["cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            result = app_mod._get_session_process(proc.pid)
            assert result == "cat", f"Expected 'cat', got '{result}'"
        finally:
            proc.kill()
            proc.wait()

    def test_returns_unknown_for_dead_pid(self):
        """Return 'unknown' when the PID does not exist."""
        app_mod = _get_app()

        # Use a PID that almost certainly doesn't exist
        result = app_mod._get_session_process(999999999)
        assert result == "unknown"

    def test_returns_unknown_for_invalid_pid(self):
        """Return 'unknown' for negative or zero PIDs."""
        app_mod = _get_app()

        assert app_mod._get_session_process(-1) == "unknown"
        assert app_mod._get_session_process(0) == "unknown"
