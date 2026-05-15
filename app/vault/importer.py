"""
Vault importer — full file → chunks → embeddings → DB pipeline.

All operations are strictly local. No network calls are made here.

Supported formats:
- Plain text / Markdown / source code (.txt, .md, .py, .js, …)
- PDF (.pdf) via pypdf (optional dependency — graceful fallback if missing)
- Everything else: best-effort UTF-8 decode
"""

from __future__ import annotations

import hashlib
import time
import uuid

from app.database import connection
from app.vault.chunker import chunk_text
from app.vault.embedder import embed, serialize


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(data: bytes, mime: str, filename: str) -> str:
    """Extract plain text from raw file bytes."""
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        return _extract_pdf(data)
    # Default: UTF-8 with replacement characters for undecodable bytes
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        # pypdf not installed — fall back to raw decode
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class DuplicateFileError(Exception):
    """Raised when a file with the same SHA-256 hash already exists in the vault."""


def import_file(
    data: bytes,
    filename: str,
    mime: str,
    db_path: str,
) -> str:
    """
    Import a file into the vault.

    Steps:
      1. Hash the file for deduplication.
      2. Extract text.
      3. Chunk the text.
      4. Generate embeddings for each chunk.
      5. Write memory, chunks, and vec_chunks to the DB.

    Returns the new memory_id (UUID string).
    Raises DuplicateFileError if the file hash is already present.
    Raises ValueError if the file produces no extractable text.
    """
    file_hash = hashlib.sha256(data).hexdigest()
    now = time.time()
    memory_id = str(uuid.uuid4())

    with connection(db_path) as conn:
        # Deduplication check
        existing = conn.execute(
            "SELECT id FROM memories WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if existing:
            raise DuplicateFileError(existing["id"])

        # Extract + validate
        text = extract_text(data, mime, filename)
        if not text.strip():
            raise ValueError("No extractable text found in the uploaded file.")

        # Chunk
        raw_chunks = chunk_text(text)
        if not raw_chunks:
            raise ValueError("File text produced no chunks.")

        # Embed all chunks in one batch call
        texts = [c[0] for c in raw_chunks]
        vectors = embed(texts)

        # Write memory record
        conn.execute(
            "INSERT INTO memories "
            "(id, filename, file_hash, mime_type, file_size, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'indexed', ?, ?)",
            (memory_id, filename, file_hash, mime, len(data), now, now),
        )

        # Write chunks + vec_chunks
        for i, ((chunk_text_str, start, end), vector) in enumerate(zip(raw_chunks, vectors)):
            chunk_id = str(uuid.uuid4())
            token_count = len(chunk_text_str.split())

            cursor = conn.execute(
                "INSERT INTO chunks "
                "(id, memory_id, chunk_index, text, start_offset, end_offset, token_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk_id, memory_id, i, chunk_text_str, start, end, token_count, now),
            )
            rowid = cursor.lastrowid

            conn.execute(
                "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize(vector)),
            )

        conn.commit()

    return memory_id
