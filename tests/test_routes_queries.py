"""
Tests for GET /queries, GET /queries/stats, GET /queries/{id}.
"""

import time
import uuid

import pytest

from app.database import get_connection


def _insert_query(db_path, routing="local-only"):
    qid = str(uuid.uuid4())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO queries (id, query_text, routing_decision, created_at) VALUES (?, ?, ?, ?)",
            (qid, "test query", routing, time.time()),
        )
        conn.commit()
    return qid


# ---------------------------------------------------------------------------
# GET /queries
# ---------------------------------------------------------------------------

def test_list_queries_empty(client):
    resp = client.get("/queries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["queries"] == []


def test_list_queries_returns_entries(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.queries.DB_PATH", tmp_db)
    _insert_query(tmp_db, "local-only")
    _insert_query(tmp_db, "guarded-online")

    resp = client.get("/queries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2


def test_list_queries_pagination(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.queries.DB_PATH", tmp_db)
    for _ in range(5):
        _insert_query(tmp_db)

    resp = client.get("/queries", params={"limit": 3, "offset": 0})
    assert len(resp.json()["queries"]) == 3
    assert resp.json()["total"] == 5


def test_list_queries_newest_first(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.queries.DB_PATH", tmp_db)
    _insert_query(tmp_db)
    time.sleep(0.01)
    qid_newer = _insert_query(tmp_db)

    resp = client.get("/queries")
    assert resp.json()["queries"][0]["id"] == qid_newer


# ---------------------------------------------------------------------------
# GET /queries/stats
# ---------------------------------------------------------------------------

def test_routing_stats_empty(client):
    resp = client.get("/queries/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["local_only"] == 0


def test_routing_stats_counts(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.queries.DB_PATH", tmp_db)
    _insert_query(tmp_db, "local-only")
    _insert_query(tmp_db, "local-only")
    _insert_query(tmp_db, "guarded-online")
    _insert_query(tmp_db, "blocked")

    resp = client.get("/queries/stats")
    data = resp.json()
    assert data["local_only"] == 2
    assert data["guarded_online"] == 1
    assert data["blocked"] == 1
    assert data["total"] == 4


# ---------------------------------------------------------------------------
# GET /queries/{id}
# ---------------------------------------------------------------------------

def test_get_query_by_id(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.queries.DB_PATH", tmp_db)
    qid = _insert_query(tmp_db, "guarded-online")

    resp = client.get(f"/queries/{qid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == qid
    assert resp.json()["routing_decision"] == "guarded-online"


def test_get_query_not_found(client):
    resp = client.get("/queries/nonexistent-id")
    assert resp.status_code == 404
