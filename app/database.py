import sqlite3
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from app.config import BUSY_TIMEOUT_MS, DB_PATH, EMBEDDING_DIM


def _configure(conn: sqlite3.Connection) -> None:
    """Apply PRAGMAs and load sqlite-vec on every new connection."""
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")


def get_connection(path: str = DB_PATH) -> sqlite3.Connection:
    """Return a configured sqlite3 connection. Caller is responsible for closing."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _configure(conn)
    return conn


@contextmanager
def connection(path: str = DB_PATH):
    """Context manager that yields a configured connection and closes it on exit."""
    conn = get_connection(path)
    try:
        yield conn
    finally:
        conn.close()


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    file_hash   TEXT NOT NULL UNIQUE,
    mime_type   TEXT NOT NULL,
    file_size   INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    error_msg   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,
    memory_id       TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    start_offset    INTEGER NOT NULL,
    end_offset      INTEGER NOT NULL,
    token_count     INTEGER NOT NULL,
    retrieval_weight REAL NOT NULL DEFAULT 1.0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'queued',
    attempts    INTEGER NOT NULL DEFAULT 0,
    error_msg   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    id               TEXT PRIMARY KEY,
    query_text       TEXT NOT NULL,
    routing_decision TEXT NOT NULL DEFAULT 'local-only',
    created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrievals (
    id              TEXT PRIMARY KEY,
    query_id        TEXT NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    chunk_id        TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    cosine_score    REAL NOT NULL,
    rank_score      REAL NOT NULL,
    feedback        TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS query_classifications (
    id               TEXT PRIMARY KEY,
    query_id         TEXT NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    privacy_level    TEXT NOT NULL,
    private_subquery TEXT,
    public_subquery  TEXT,
    classifier_used  TEXT NOT NULL,
    confidence       REAL,
    routing_decision TEXT NOT NULL,
    created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS network_audit_log (
    id               TEXT PRIMARY KEY,
    timestamp        REAL NOT NULL,
    mode             TEXT NOT NULL,
    destination      TEXT NOT NULL,
    query_id         TEXT REFERENCES queries(id),
    payload_preview  TEXT NOT NULL,
    response_status  INTEGER,
    user_consented   INTEGER NOT NULL DEFAULT 0,
    privacy_level    TEXT,
    leak_detected    INTEGER NOT NULL DEFAULT 0,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    embedding float[{EMBEDDING_DIM}]
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chunks_memory_id  ON chunks(memory_id);
CREATE INDEX IF NOT EXISTS idx_jobs_memory_id     ON jobs(memory_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status        ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_retrievals_query   ON retrievals(query_id);
CREATE INDEX IF NOT EXISTS idx_qc_query_id        ON query_classifications(query_id);
CREATE INDEX IF NOT EXISTS idx_nal_timestamp      ON network_audit_log(timestamp);
"""


def init_db(path: str = DB_PATH) -> None:
    """Create all tables and indexes if they don't exist."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_INDEXES)
        conn.commit()


_EXPECTED_TABLES = {
    "memories",
    "chunks",
    "jobs",
    "queries",
    "retrievals",
    "query_classifications",
    "network_audit_log",
    "vec_chunks",
}


def get_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
        "UNION SELECT name FROM sqlite_schema WHERE type = 'table'"
    ).fetchall()
    # sqlite_master covers regular + virtual tables
    rows2 = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r["name"] for r in rows2}
