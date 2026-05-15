"""
Day 1 tests — database foundation.

Covers:
- WAL mode enabled
- Foreign keys enforced
- All expected tables present (including vec_chunks virtual table)
- busy_timeout configured
- Mode 1 zero-network-calls invariant (no network_audit_log entries after local ops)
- sqlite-vec round-trip: insert + cosine distance search
"""

import sqlite3
import time

import pytest

from app.database import _EXPECTED_TABLES, get_connection, get_table_names, init_db


# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------

def test_wal_mode_enabled(conn):
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

def test_foreign_keys_enforced(conn):
    """Inserting a chunk with a non-existent memory_id must raise IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chunks (id, memory_id, chunk_index, text, "
            "start_offset, end_offset, token_count, created_at) "
            "VALUES ('c1', 'nonexistent-memory', 0, 'hello', 0, 5, 1, ?)",
            (time.time(),),
        )


def test_foreign_key_cascade_delete(conn):
    """Deleting a memory must cascade to its chunks."""
    now = time.time()
    conn.execute(
        "INSERT INTO memories (id, filename, file_hash, mime_type, file_size, created_at, updated_at) "
        "VALUES ('m1', 'test.pdf', 'abc123', 'application/pdf', 100, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO chunks (id, memory_id, chunk_index, text, "
        "start_offset, end_offset, token_count, created_at) "
        "VALUES ('ch1', 'm1', 0, 'some text', 0, 9, 2, ?)",
        (now,),
    )
    conn.commit()
    conn.execute("DELETE FROM memories WHERE id = 'm1'")
    conn.commit()
    row = conn.execute("SELECT COUNT(*) FROM chunks WHERE memory_id = 'm1'").fetchone()
    assert row[0] == 0


# ---------------------------------------------------------------------------
# Schema completeness
# ---------------------------------------------------------------------------

def test_all_expected_tables_exist(conn):
    tables = get_table_names(conn)
    missing = _EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


def test_memories_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    required = {"id", "filename", "file_hash", "mime_type", "file_size", "status", "created_at", "updated_at"}
    assert required <= cols


def test_chunks_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    required = {"id", "memory_id", "chunk_index", "text", "start_offset", "end_offset", "token_count", "retrieval_weight", "created_at"}
    assert required <= cols


def test_queries_has_routing_decision_column(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(queries)").fetchall()}
    assert "routing_decision" in cols


def test_network_audit_log_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(network_audit_log)").fetchall()}
    required = {"id", "timestamp", "mode", "destination", "query_id", "payload_preview",
                "response_status", "user_consented", "privacy_level", "leak_detected", "created_at"}
    assert required <= cols


def test_query_classifications_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(query_classifications)").fetchall()}
    required = {"id", "query_id", "privacy_level", "private_subquery", "public_subquery",
                "classifier_used", "confidence", "routing_decision", "created_at"}
    assert required <= cols


# ---------------------------------------------------------------------------
# busy_timeout
# ---------------------------------------------------------------------------

def test_busy_timeout_configured(conn):
    """busy_timeout should be set to a positive value (≥5000ms)."""
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] >= 5000


# ---------------------------------------------------------------------------
# Mode 1 zero-network-calls invariant
# ---------------------------------------------------------------------------

def test_mode1_zero_network_audit_entries(tmp_db):
    """
    After initialising the DB and performing local operations,
    network_audit_log must be empty.
    This is the Mode 1 zero-network invariant: no external calls are ever
    logged unless the caller explicitly inserts a row.
    """
    conn = get_connection(tmp_db)
    # Simulate a local ingest + query without any external call
    now = time.time()
    conn.execute(
        "INSERT INTO memories (id, filename, file_hash, mime_type, file_size, created_at, updated_at) "
        "VALUES ('m2', 'private.pdf', 'sha256abc', 'application/pdf', 512, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO queries (id, query_text, routing_decision, created_at) VALUES ('q1', 'what is in my notes', 'local-only', ?)",
        (now,),
    )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 0, f"Expected 0 audit entries in Mode 1, got {count}"


# ---------------------------------------------------------------------------
# sqlite-vec round-trip
# ---------------------------------------------------------------------------

def test_sqlite_vec_insert_and_search(conn):
    """Insert a vector and retrieve it via cosine distance."""
    import struct

    dim = 768
    vec_a = [0.1] * dim
    vec_b = [0.1] * dim  # identical → distance ≈ 0

    def serialize(v):
        return struct.pack(f"{len(v)}f", *v)

    conn.execute(
        "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
        (1, serialize(vec_a)),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = 1",
        (serialize(vec_b),),
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["rowid"] == 1
    assert rows[0]["distance"] == pytest.approx(0.0, abs=1e-5)


def test_sqlite_vec_cosine_distance_different_vectors(conn):
    """Two clearly different vectors should have non-zero distance."""
    import struct

    dim = 768

    def serialize(v):
        return struct.pack(f"{len(v)}f", *v)

    # Normalised orthogonal-ish vectors
    vec_a = [1.0] + [0.0] * (dim - 1)
    vec_b = [0.0, 1.0] + [0.0] * (dim - 2)

    conn.execute("INSERT INTO vec_chunks (rowid, embedding) VALUES (1, ?)", (serialize(vec_a),))
    conn.commit()

    rows = conn.execute(
        "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = 1",
        (serialize(vec_b),),
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["distance"] > 0.5  # clearly dissimilar


# ---------------------------------------------------------------------------
# Health endpoint (smoke test)
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
