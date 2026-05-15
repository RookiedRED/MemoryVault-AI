"""
Tests for GET /audit/log and GET /audit/log/{query_id}.
"""

import time
import uuid

import pytest

from app.database import get_connection


def _insert_audit_entry(db_path: str, query_id: str = None) -> str:
    """Helper: insert a fake audit entry and return its query_id."""
    qid = query_id or str(uuid.uuid4())
    now = time.time()
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO queries (id, query_text, routing_decision, created_at) "
            "VALUES (?, ?, ?, ?)",
            (qid, "test query", "guarded-online", now),
        )
        conn.execute(
            "INSERT INTO network_audit_log "
            "(id, timestamp, mode, destination, query_id, payload_preview, "
            "response_status, user_consented, privacy_level, leak_detected, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), now, "guarded-online", "openai_gpt4o",
             qid, '{"sanitized_context": "..."}', 200, 1, "public", 0, now),
        )
        conn.commit()
    return qid


# ---------------------------------------------------------------------------
# GET /audit/log
# ---------------------------------------------------------------------------

def test_audit_log_empty(client):
    resp = client.get("/audit/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["total"] == 0


def test_audit_log_returns_entries(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.audit.DB_PATH", tmp_db)
    _insert_audit_entry(tmp_db)
    resp = client.get("/audit/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["mode"] == "guarded-online"
    assert entry["leak_detected"] is False


def test_audit_log_pagination(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.audit.DB_PATH", tmp_db)
    for _ in range(5):
        _insert_audit_entry(tmp_db)
    resp = client.get("/audit/log", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["entries"]) == 2


# ---------------------------------------------------------------------------
# GET /audit/log/{query_id}
# ---------------------------------------------------------------------------

def test_audit_entry_by_query_id(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.audit.DB_PATH", tmp_db)
    qid = _insert_audit_entry(tmp_db)
    resp = client.get(f"/audit/log/{qid}")
    assert resp.status_code == 200
    assert resp.json()["query_id"] == qid


def test_audit_entry_not_found(client):
    resp = client.get("/audit/log/nonexistent-id")
    assert resp.status_code == 404
