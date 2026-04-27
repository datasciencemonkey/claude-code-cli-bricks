"""Tests for memory/store.py — content hashing, length cap, schema constants.

Excludes the actual psycopg connection pool / Lakebase round-trip — those
require live workspace credentials and are covered by the manual e2e
verification in PR #145. Here we cover the pure-Python parts that can
regress in isolation.
"""

from unittest import mock


class TestContentHash:
    """_content_hash — deterministic, case- and whitespace-insensitive."""

    def _hash(self):
        from memory.store import _content_hash
        return _content_hash

    def test_same_input_same_hash(self):
        assert self._hash()("hello") == self._hash()("hello")

    def test_case_insensitive(self):
        # Dedup should treat "User prefers uv" and "user prefers uv" identically.
        assert self._hash()("User prefers uv") == self._hash()("user prefers uv")

    def test_whitespace_stripped(self):
        # Leading/trailing whitespace shouldn't generate a duplicate row.
        assert self._hash()("  hello  ") == self._hash()("hello")

    def test_different_content_different_hash(self):
        assert self._hash()("alpha") != self._hash()("beta")

    def test_returns_sha256_hex(self):
        # 64-char hex string.
        h = self._hash()("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestMaxContentLength:
    """_MAX_CONTENT_LEN constant + upsert rejects oversized content."""

    def test_constant_is_500(self):
        from memory.store import _MAX_CONTENT_LEN
        # Generous for a "one sentence" memory; 500 is the documented cap.
        assert _MAX_CONTENT_LEN == 500

    def test_upsert_rejects_oversized_content(self, capsys):
        # Mock the connection pool so we don't need Lakebase.
        from memory import store

        mock_cur = mock.MagicMock()
        mock_cur.rowcount = 1
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value = mock_cur
        mock_pool = mock.MagicMock()
        mock_pool.connection.return_value.__enter__.return_value = mock_conn

        with mock.patch.object(store, "_get_pool", return_value=mock_pool):
            oversized = "x" * 600  # > 500-char cap
            normal = "User prefers uv"
            count = store.upsert_memories(
                memories=[
                    {"type": "feedback", "content": oversized, "importance": 0.9},
                    {"type": "feedback", "content": normal, "importance": 0.9},
                ],
                owner_email="test@databricks.com",
                project_name="demo",
                session_id="s1",
            )

        # Only the normal memory was written.
        assert count == 1
        # Stderr explains why the oversized one was dropped.
        err = capsys.readouterr().err
        assert "[memory-store] rejected memory" in err
        assert "600 chars" in err

    def test_upsert_skips_empty_content(self):
        from memory import store

        mock_cur = mock.MagicMock()
        mock_cur.rowcount = 1
        mock_conn = mock.MagicMock()
        mock_conn.execute.return_value = mock_cur
        mock_pool = mock.MagicMock()
        mock_pool.connection.return_value.__enter__.return_value = mock_conn

        with mock.patch.object(store, "_get_pool", return_value=mock_pool):
            count = store.upsert_memories(
                memories=[
                    {"type": "feedback", "content": "", "importance": 0.5},
                    {"type": "feedback", "content": "   ", "importance": 0.5},  # whitespace-only
                ],
                owner_email="test@databricks.com",
                project_name=None,
                session_id=None,
            )
        # Nothing written; empties are silently skipped.
        assert count == 0

    def test_upsert_empty_list_returns_zero_without_pool_use(self):
        from memory import store

        with mock.patch.object(store, "_get_pool") as mock_pool:
            count = store.upsert_memories(
                memories=[],
                owner_email="test@databricks.com",
                project_name=None,
                session_id=None,
            )
        assert count == 0
        # Empty input should short-circuit; no pool access.
        mock_pool.assert_not_called()


class TestSchemaSql:
    """SCHEMA_SQL has the load-bearing pieces required for the feature."""

    def test_schema_has_required_pieces(self):
        from memory.store import SCHEMA_SQL
        # pgvector extension for future semantic recall
        assert "CREATE EXTENSION IF NOT EXISTS vector" in SCHEMA_SQL
        # Dedup index — the whole point of content_hash
        assert "idx_coda_mem_dedup" in SCHEMA_SQL
        assert "UNIQUE INDEX" in SCHEMA_SQL
        # FTS index for search
        assert "idx_coda_mem_fts" in SCHEMA_SQL
        assert "USING GIN" in SCHEMA_SQL
        # HNSW index for vector search (gated WHERE embedding IS NOT NULL)
        assert "idx_coda_mem_vec" in SCHEMA_SQL
        assert "USING hnsw" in SCHEMA_SQL
