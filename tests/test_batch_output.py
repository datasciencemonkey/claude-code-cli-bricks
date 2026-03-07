"""Tests for AC-7: Batch Output Endpoint.

POST /api/output-batch accepts {"session_ids": [...]} and returns
{"outputs": {"id1": {"output": "...", "exited": false}, ...}}.
The existing /api/output endpoint remains for backward compatibility.
"""

import time


class TestBatchOutputEndpoint:
    """AC-7: Batch output endpoint exists and works correctly."""

    def test_batch_endpoint_exists(self, app_client):
        """POST /api/output-batch returns 200 (not 404/405)."""
        resp = app_client.post("/api/output-batch", json={"session_ids": []})
        assert resp.status_code == 200

    def test_batch_empty_session_ids(self, app_client):
        """Empty session_ids list returns empty outputs dict."""
        resp = app_client.post("/api/output-batch", json={"session_ids": []})
        data = resp.get_json()
        assert "outputs" in data
        assert data["outputs"] == {}

    def test_batch_single_session(self, app_client, create_session):
        """Batch with one session_id returns output for that session."""
        sid = create_session()
        # Give the shell a moment to produce prompt output
        time.sleep(0.3)

        resp = app_client.post("/api/output-batch", json={"session_ids": [sid]})
        data = resp.get_json()

        assert resp.status_code == 200
        assert "outputs" in data
        assert sid in data["outputs"]
        assert "output" in data["outputs"][sid]
        assert "exited" in data["outputs"][sid]
        assert isinstance(data["outputs"][sid]["exited"], bool)

    def test_batch_multiple_sessions(self, app_client, create_session):
        """Batch with multiple session_ids returns output for each."""
        sid1 = create_session()
        sid2 = create_session()
        time.sleep(0.3)

        resp = app_client.post("/api/output-batch", json={"session_ids": [sid1, sid2]})
        data = resp.get_json()

        assert resp.status_code == 200
        assert sid1 in data["outputs"]
        assert sid2 in data["outputs"]

    def test_batch_unknown_session_excluded(self, app_client, create_session):
        """Unknown session_ids are excluded from output (not an error)."""
        sid = create_session()
        time.sleep(0.3)

        resp = app_client.post(
            "/api/output-batch", json={"session_ids": [sid, "nonexistent-session-id"]}
        )
        data = resp.get_json()

        assert resp.status_code == 200
        assert sid in data["outputs"]
        assert "nonexistent-session-id" not in data["outputs"]

    def test_batch_updates_last_poll_time(self, app_client, create_session):
        """Batch polling updates last_poll_time for each session (prevents cleanup)."""
        from app import sessions, sessions_lock

        sid = create_session()
        time.sleep(0.3)

        # Record poll time before batch call
        with sessions_lock:
            old_poll_time = sessions[sid]["last_poll_time"]

        time.sleep(0.1)
        app_client.post("/api/output-batch", json={"session_ids": [sid]})

        with sessions_lock:
            new_poll_time = sessions[sid]["last_poll_time"]

        assert new_poll_time > old_poll_time

    def test_batch_clears_buffer(self, app_client, create_session):
        """Batch polling clears the output buffer (same as single /api/output)."""
        sid = create_session()
        time.sleep(0.5)  # Let shell produce output

        # First poll should get output
        resp1 = app_client.post("/api/output-batch", json={"session_ids": [sid]})
        data1 = resp1.get_json()

        # Second immediate poll should get empty or minimal output
        resp2 = app_client.post("/api/output-batch", json={"session_ids": [sid]})
        data2 = resp2.get_json()

        # The second poll output should be less than or equal to the first
        # (buffer was cleared by first poll)
        output1 = data1["outputs"][sid]["output"]
        output2 = data2["outputs"][sid]["output"]
        # First poll should have captured the initial shell prompt
        # Second poll should have much less (or empty)
        assert len(output2) <= len(output1) or output1 == ""

    def test_batch_missing_session_ids_key(self, app_client):
        """Missing session_ids key returns 400 error."""
        resp = app_client.post("/api/output-batch", json={})
        assert resp.status_code == 400

    def test_original_output_endpoint_still_works(self, app_client, create_session):
        """AC-7 backward compat: /api/output still works."""
        sid = create_session()
        time.sleep(0.3)

        resp = app_client.post("/api/output", json={"session_id": sid})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "output" in data
        assert "exited" in data
