"""
Tests for memory management routes:
GET /vault/memories, GET /vault/memories/{id}, DELETE /vault/memories/{id}
"""

import io
from unittest.mock import patch

import pytest

from app.database import get_connection
from app.vault.importer import import_file


def _fake_embed(texts):
    return [[0.0] * 768 for _ in texts]


def _import(tmp_db, content=b"test content here", filename="test.txt"):
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        return import_file(content, filename, "text/plain", tmp_db)


# ---------------------------------------------------------------------------
# GET /vault/memories
# ---------------------------------------------------------------------------

def test_list_memories_empty(client):
    resp = client.get("/vault/memories")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["memories"] == []


def test_list_memories_after_import(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    _import(tmp_db, b"first document content", "first.txt")
    _import(tmp_db, b"second document content", "second.txt")

    resp = client.get("/vault/memories")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["memories"]) == 2


def test_list_memories_includes_chunk_count(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    _import(tmp_db, b"some content to chunk", "doc.txt")

    resp = client.get("/vault/memories")
    memory = resp.json()["memories"][0]
    assert memory["chunk_count"] >= 1


def test_list_memories_pagination(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    for i in range(5):
        _import(tmp_db, f"content {i}".encode(), f"file{i}.txt")

    resp = client.get("/vault/memories", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["memories"]) == 2


# ---------------------------------------------------------------------------
# GET /vault/memories/{id}
# ---------------------------------------------------------------------------

def test_get_memory_returns_detail(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    memory_id = _import(tmp_db, b"detail test content", "detail.txt")

    resp = client.get(f"/vault/memories/{memory_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == memory_id
    assert data["filename"] == "detail.txt"
    assert len(data["chunks"]) >= 1
    assert "text" in data["chunks"][0]


def test_get_memory_not_found(client):
    resp = client.get("/vault/memories/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /vault/memories/{id}
# ---------------------------------------------------------------------------

def test_delete_memory_returns_204(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    memory_id = _import(tmp_db, b"delete me content", "delete.txt")

    resp = client.delete(f"/vault/memories/{memory_id}")
    assert resp.status_code == 204


def test_delete_memory_removes_from_db(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    memory_id = _import(tmp_db, b"to be deleted", "gone.txt")

    client.delete(f"/vault/memories/{memory_id}")

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    assert row is None


def test_delete_memory_cascades_to_chunks(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    memory_id = _import(tmp_db, b"cascade test content", "cascade.txt")

    client.delete(f"/vault/memories/{memory_id}")

    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM chunks WHERE memory_id = ?", (memory_id,)).fetchone()[0]
    conn.close()
    assert count == 0


def test_delete_memory_not_found(client):
    resp = client.delete("/vault/memories/nonexistent-id")
    assert resp.status_code == 404
