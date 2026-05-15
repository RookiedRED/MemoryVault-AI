"""
Tests for app/vault/importer.py — text extraction, chunking, DB writes.
The embedder is mocked so no GPU / model download is required.
"""

import struct
import time
import uuid
from unittest.mock import patch

import pytest

from app.database import get_connection
from app.vault.importer import DuplicateFileError, extract_text, import_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embed(texts):
    """Return a list of deterministic 768-dim zero vectors."""
    return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------

def test_extract_plain_text():
    data = b"Hello, this is a plain text file."
    assert extract_text(data, "text/plain", "note.txt") == "Hello, this is a plain text file."


def test_extract_replaces_invalid_utf8():
    data = b"valid \xff\xfe bytes"
    result = extract_text(data, "text/plain", "file.txt")
    assert "valid" in result


def test_extract_pdf_falls_back_gracefully_if_pypdf_missing(monkeypatch):
    # Simulate pypdf not being installed
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)
    data = b"not a real pdf"
    result = extract_text(data, "application/pdf", "doc.pdf")
    # Should not raise; returns best-effort decode
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# import_file — DB writes
# ---------------------------------------------------------------------------

def test_import_creates_memory_record(tmp_db):
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        memory_id = import_file(b"Sprint notes content", "sprint.txt", "text/plain", tmp_db)

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["filename"] == "sprint.txt"
    assert row["status"] == "indexed"


def test_import_creates_chunks(tmp_db):
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        memory_id = import_file(b"Some text content for chunking", "doc.txt", "text/plain", tmp_db)

    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM chunks WHERE memory_id = ?", (memory_id,)).fetchone()[0]
    conn.close()
    assert count >= 1


def test_import_creates_vec_chunks(tmp_db):
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        memory_id = import_file(b"Embedding test content", "vec.txt", "text/plain", tmp_db)

    conn = get_connection(tmp_db)
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE memory_id = ?", (memory_id,)).fetchone()[0]
    vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    conn.close()
    assert vec_count == chunk_count


def test_import_duplicate_raises(tmp_db):
    content = b"Unique document content"
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        import_file(content, "a.txt", "text/plain", tmp_db)
        with pytest.raises(DuplicateFileError):
            import_file(content, "b.txt", "text/plain", tmp_db)


def test_import_empty_text_raises(tmp_db):
    # A file with only whitespace produces no chunks
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        with pytest.raises(ValueError):
            import_file(b"   \n  \t  ", "blank.txt", "text/plain", tmp_db)


def test_import_large_file_produces_multiple_chunks(tmp_db):
    # ~6000 chars → at least 3 chunks with default CHUNK_SIZE=1500
    content = ("word " * 1500).encode()
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        memory_id = import_file(content, "big.txt", "text/plain", tmp_db)

    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM chunks WHERE memory_id = ?", (memory_id,)).fetchone()[0]
    conn.close()
    assert count >= 3


def test_import_returns_uuid_string(tmp_db):
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        memory_id = import_file(b"uuid test", "u.txt", "text/plain", tmp_db)
    # Should be a valid UUID
    uuid.UUID(memory_id)
