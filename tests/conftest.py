"""Shared fixtures for terminal app tests."""

import os
import sys
import pytest

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app_client():
    """Create a Flask test client with mocked initialization.

    We import app but skip initialize_app() -- tests exercise routes directly.
    The app module sets up sessions dict and routes at import time.
    """
    # Prevent Databricks SDK imports from failing in test env
    os.environ.setdefault("DATABRICKS_HOST", "https://test.databricks.com")
    os.environ.setdefault("DATABRICKS_TOKEN", "dapi_test_token_12345")

    from app import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def create_session(app_client):
    """Helper fixture: creates a PTY session and returns the session_id.

    Cleans up after the test.
    """
    created_ids = []

    def _create():
        resp = app_client.post("/api/session")
        data = resp.get_json()
        assert "session_id" in data, f"Failed to create session: {data}"
        created_ids.append(data["session_id"])
        return data["session_id"]

    yield _create

    # Cleanup: close all created sessions
    for sid in created_ids:
        try:
            app_client.post("/api/session/close", json={"session_id": sid})
        except Exception:
            pass
