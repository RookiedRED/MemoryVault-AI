"""
Tests for POST /vault/import and GET /vault/status.
Embedder is mocked so no model download is required.
"""

import io
from unittest.mock import patch

import pytest

from app.database import get_connection


def _fake_embed(texts):
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# GET /vault/status
# ---------------------------------------------------------------------------

def test_vault_status_empty(client):
    resp = client.get("/vault/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["memory_count"] == 0
    assert data["chunk_count"] == 0


def test_vault_status_after_import(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    content = b"Hello vault world"
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        resp = client.post(
            "/vault/import",
            files={"file": ("note.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /vault/import
# ---------------------------------------------------------------------------

def test_import_returns_memory_id(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    content = b"Sprint 2 notes"
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        resp = client.post(
            "/vault/import",
            files={"file": ("sprint2.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "imported"
    assert "memory_id" in data
    assert data["filename"] == "sprint2.txt"
    assert data["file_size"] == len(content)


def test_import_duplicate_rejected(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    content = b"Unique content here"
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        client.post(
            "/vault/import",
            files={"file": ("dup.txt", io.BytesIO(content), "text/plain")},
        )
        resp = client.post(
            "/vault/import",
            files={"file": ("dup.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 409


def test_import_empty_file_rejected(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    resp = client.post(
        "/vault/import",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert resp.status_code == 400


def test_import_written_to_db(client, tmp_db, monkeypatch):
    monkeypatch.setattr("app.routes.vault.DB_PATH", tmp_db)
    content = b"Database check content"
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        resp = client.post(
            "/vault/import",
            files={"file": ("dbcheck.txt", io.BytesIO(content), "text/plain")},
        )
    assert resp.status_code == 200
    memory_id = resp.json()["memory_id"]

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["filename"] == "dbcheck.txt"
