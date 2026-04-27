"""Lakebase-backed persistent memory store for CODA sessions.

Follows the canonical Databricks Apps + Lakebase Autoscaling pattern:
https://docs.databricks.com/aws/en/oltp/projects/tutorial-databricks-apps-autoscaling

Token rotation is handled automatically by psycopg_pool recycling connections
at 45 min (max_lifetime=2700). OAuthConnection.connect() fires on each recycle
and fetches a fresh Lakebase-scoped credential via the Databricks SDK.

Required env vars (auto-injected by the app's database resource):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGSSLMODE

Required env var (set manually in app.yaml):
    ENDPOINT_NAME — full Autoscaling endpoint resource path:
                    projects/{project_id}/branches/{branch_id}/endpoints/{endpoint_id}
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg_pool import ConnectionPool

# Memory-injection defense: cap content length at write-time. The extraction
# prompt asks Haiku for "one sentence per memory" — 500 chars is generous and
# catches the case where Haiku gets confused by a long block and extracts a
# whole paragraph (a common entry path for indirect prompt injection).
_MAX_CONTENT_LEN = 500

_sdk_client: WorkspaceClient | None = None
_db_user: str | None = None
_pool: ConnectionPool | None = None


def _get_sdk() -> WorkspaceClient:
    """Return a WorkspaceClient bound to ~/.databrickscfg.

    Terminal sessions strip DATABRICKS_TOKEN from env (so the PAT rotator's
    file is the source of truth). Passing profile= forces the SDK to use
    config-file auth instead of env-based auth, which would otherwise fail
    because DATABRICKS_HOST is present in env but the token is not.
    """
    global _sdk_client
    if _sdk_client is None:
        profile = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
        _sdk_client = WorkspaceClient(profile=profile)
    return _sdk_client


class OAuthConnection(psycopg.Connection):
    """psycopg.Connection subclass that mints a fresh Lakebase OAuth token
    and sets the matching username every time the pool opens a new connection.

    We override both `password` AND `user` here. PGUSER (auto-injected by the
    postgres app resource) is the SP's role, but since terminal subprocesses
    authenticate as the human user via the rotator's PAT, the credential's
    identity is the user's — so user= must match.
    """

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> "OAuthConnection":
        global _db_user
        sdk = _get_sdk()
        # .strip() guards against trailing newlines/whitespace — can slip in if
        # the secret was stored via `echo ... | put-secret` (adds a \n).
        endpoint_name = os.environ["ENDPOINT_NAME"].strip()

        if _db_user is None:
            _db_user = sdk.current_user.me().user_name
        kwargs["user"] = _db_user

        cred = sdk.postgres.generate_database_credential(endpoint=endpoint_name)
        kwargs["password"] = cred.token
        return super().connect(conninfo, **kwargs)


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS coda_memories (
    id           SERIAL PRIMARY KEY,
    owner_email  TEXT NOT NULL,
    project_name TEXT,
    session_id   TEXT,
    memory_type  TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT,
    content_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding    vector(1536),
    importance   FLOAT DEFAULT 0.5,
    created_at   TIMESTAMPTZ DEFAULT now(),
    updated_at   TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_coda_mem_dedup
    ON coda_memories (owner_email, content_hash);

CREATE INDEX IF NOT EXISTS idx_coda_mem_owner
    ON coda_memories (owner_email);

CREATE INDEX IF NOT EXISTS idx_coda_mem_owner_proj
    ON coda_memories (owner_email, project_name);

CREATE INDEX IF NOT EXISTS idx_coda_mem_created
    ON coda_memories (owner_email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_coda_mem_fts
    ON coda_memories USING GIN (content_tsv);

CREATE INDEX IF NOT EXISTS idx_coda_mem_vec
    ON coda_memories USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
"""


def _get_pool() -> ConnectionPool:
    """Lazy-init the connection pool. Idempotent across calls."""
    global _pool
    if _pool is not None:
        return _pool

    host = os.environ["PGHOST"]
    port = os.environ.get("PGPORT", "5432")
    database = os.environ["PGDATABASE"]
    sslmode = os.environ.get("PGSSLMODE", "require")

    # user= is injected per-connection by OAuthConnection.connect() so it matches
    # the identity of the OAuth credential. PGUSER (SP client ID) would be wrong
    # since terminal subprocesses authenticate as the user, not the SP.
    conninfo = f"dbname={database} host={host} port={port} sslmode={sslmode}"

    _pool = ConnectionPool(
        conninfo=conninfo,
        connection_class=OAuthConnection,
        min_size=1,
        max_size=5,
        # Recycle connections 15 min before the 1-hour token expiry.
        # OAuthConnection.connect() fetches a fresh token on each recycle.
        max_lifetime=2700,
        open=True,
    )
    return _pool


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().lower().encode()).hexdigest()


def ensure_schema() -> None:
    with _get_pool().connection() as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()


def upsert_memories(
    memories: list[dict[str, Any]],
    owner_email: str,
    project_name: str | None,
    session_id: str | None,
) -> int:
    """Persist extracted memories, deduplicating by content hash. Returns insert count."""
    if not memories:
        return 0
    with _get_pool().connection() as conn:
        count = 0
        for mem in memories:
            content = mem.get("content", "").strip()
            if not content:
                continue
            if len(content) > _MAX_CONTENT_LEN:
                print(
                    f"[memory-store] rejected memory > {_MAX_CONTENT_LEN} chars "
                    f"({len(content)} chars): {content[:80]!r}...",
                    file=sys.stderr,
                )
                continue
            # psycopg 3: rowcount lives on the cursor returned by conn.execute(),
            # not on the connection itself. Capture the cursor to read it.
            cur = conn.execute(
                """
                INSERT INTO coda_memories
                    (owner_email, project_name, session_id, memory_type,
                     content, content_hash, importance)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_email, content_hash) DO NOTHING
                """,
                (
                    owner_email,
                    project_name,
                    session_id,
                    mem.get("type", "project"),
                    content,
                    _content_hash(content),
                    float(mem.get("importance", 0.5)),
                ),
            )
            count += cur.rowcount
        conn.commit()
    return count


write_memories = upsert_memories


def search_memories(
    owner_email: str,
    query: str,
    project_name: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """FTS-ranked search, project-specific results first; falls back to recency."""
    with _get_pool().connection() as conn:
        if project_name:
            rows = conn.execute(
                """
                SELECT memory_type, content, importance, created_at, project_name,
                       ts_rank(content_tsv, q) AS rank
                FROM coda_memories, plainto_tsquery('english', %s) q
                WHERE owner_email = %s
                  AND (project_name = %s OR project_name IS NULL)
                  AND content_tsv @@ q
                ORDER BY (project_name = %s) DESC, rank DESC, importance DESC
                LIMIT %s
                """,
                (query, owner_email, project_name, project_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT memory_type, content, importance, created_at, project_name,
                       ts_rank(content_tsv, q) AS rank
                FROM coda_memories, plainto_tsquery('english', %s) q
                WHERE owner_email = %s
                  AND content_tsv @@ q
                ORDER BY rank DESC, importance DESC
                LIMIT %s
                """,
                (query, owner_email, limit),
            ).fetchall()

        if not rows:
            # Fallback: recency + importance. Branch in Python rather than using
            # `%s IS NULL` in SQL (psycopg can't infer the type for a None param).
            if project_name:
                rows = conn.execute(
                    """
                    SELECT memory_type, content, importance, created_at, project_name,
                           0.0 AS rank
                    FROM coda_memories
                    WHERE owner_email = %s
                      AND (project_name = %s OR project_name IS NULL)
                    ORDER BY importance DESC, created_at DESC
                    LIMIT %s
                    """,
                    (owner_email, project_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT memory_type, content, importance, created_at, project_name,
                           0.0 AS rank
                    FROM coda_memories
                    WHERE owner_email = %s
                    ORDER BY importance DESC, created_at DESC
                    LIMIT %s
                    """,
                    (owner_email, limit),
                ).fetchall()

    return [
        {
            "type": r[0],
            "content": r[1],
            "importance": r[2],
            "created_at": r[3].isoformat() if isinstance(r[3], datetime) else str(r[3]),
            "project_name": r[4],
            "rank": float(r[5]),
        }
        for r in rows
    ]


def load_memories(
    owner_email: str,
    project_name: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Load memories ordered by importance + recency (used for MEMORY.md generation)."""
    with _get_pool().connection() as conn:
        if project_name:
            rows = conn.execute(
                """
                SELECT memory_type, content, importance, created_at, project_name
                FROM coda_memories
                WHERE owner_email = %s
                  AND (project_name = %s OR project_name IS NULL)
                ORDER BY (project_name = %s) DESC, importance DESC, created_at DESC
                LIMIT %s
                """,
                (owner_email, project_name, project_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT memory_type, content, importance, created_at, project_name
                FROM coda_memories
                WHERE owner_email = %s
                ORDER BY importance DESC, created_at DESC
                LIMIT %s
                """,
                (owner_email, limit),
            ).fetchall()

    return [
        {
            "type": r[0],
            "content": r[1],
            "importance": r[2],
            "created_at": r[3].isoformat() if isinstance(r[3], datetime) else str(r[3]),
            "project_name": r[4],
        }
        for r in rows
    ]
