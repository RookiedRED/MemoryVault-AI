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
        raw = "\n\n".join(pages)
        return _clean_pdf_text(raw)
    except ImportError:
        # pypdf not installed — fall back to raw decode
        return data.decode("utf-8", errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def _clean_pdf_text(text: str) -> str:
    """
    Fix glyph-positioning PDF artifacts where words are split into short
    letter-clusters separated by spaces:
      "pla nning with out"  → "planning without"
      "e xpe rie nce"       → "experience"
      "Softwa re Engine e r" → "Software Engineer"

    This occurs in PDFs where each glyph has an explicit x-position offset,
    causing pypdf to insert spaces between character groups. We detect runs
    of 3+ short tokens (1–6 chars each) separated by single spaces and merge
    them. To avoid merging legitimate short-word phrases, we anchor the
    pattern so it only fires when all tokens in the run are very short (≤6),
    which is atypical of normal prose.
    """
    import re

    def _merge_run(m: re.Match) -> str:
        return m.group(0).replace(" ", "")

    # Pass 1: runs of 3+ short case-insensitive tokens (1–6 chars) —
    # catches "pla nning" (3 frags), "e xpe rie nce" (4 frags), etc.
    pattern = re.compile(r'\b[A-Za-z]{1,6}(?:[ ][A-Za-z]{1,6}){2,}\b')

    def _conditional_merge(m: re.Match) -> str:
        tokens = m.group(0).split(" ")
        # Only merge if the average token length is suspiciously short (< 4.5 chars)
        # — real multi-word phrases like "United States" average ~6 chars/token
        avg_len = sum(len(t) for t in tokens) / len(tokens)
        if avg_len < 4.5:
            return "".join(tokens)
        return m.group(0)

    cleaned = pattern.sub(_conditional_merge, text)

    # Pass 2: catch 2-fragment pairs where one piece is a stub (≤3 chars):
    # "Softwa re" (6+2), "compute r" (7+1), "e xpe" (1+3), etc.
    # Only merge when the second token is a very short trailing stub (≤3 chars).
    stub_pattern = re.compile(r'\b([A-Za-z]{3,8})[ ]([A-Za-z]{1,3})\b')

    def _merge_stub(m: re.Match) -> str:
        head, tail = m.group(1), m.group(2)
        # Don't merge common English prepositions/articles/conjunctions
        _STOP = {"a", "an", "in", "on", "of", "to", "at", "by", "or", "if",
                 "is", "it", "he", "she", "we", "do", "so", "as", "be",
                 "for", "the", "and", "but", "not", "are", "was", "has"}
        if tail.lower() in _STOP or head.lower() in _STOP:
            return m.group(0)
        return head + tail

    cleaned = stub_pattern.sub(_merge_stub, cleaned)
    # Collapse double-spaces left behind after merging
    cleaned = re.sub(r'[ ]{2,}', ' ', cleaned)
    return cleaned


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
