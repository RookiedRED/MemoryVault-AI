"""
Text chunker — splits a document into overlapping character-window segments.

Each chunk is returned as a (text, start_offset, end_offset) tuple so the
original byte-position provenance is preserved in the DB.
"""

from __future__ import annotations

CHUNK_SIZE: int = 1_500   # characters per chunk
OVERLAP: int = 150        # character overlap between adjacent chunks


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> list[tuple[str, int, int]]:
    """
    Split *text* into overlapping windows.

    Returns a list of (chunk_text, start_offset, end_offset) tuples.
    Chunks are split on whitespace boundaries when possible so words
    are not torn in half.
    """
    if not text:
        return []

    chunks: list[tuple[str, int, int]] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)

        # Extend to the next whitespace boundary (avoid splitting mid-word)
        if end < text_len:
            boundary = text.rfind(" ", start, end + 1)
            if boundary > start:
                end = boundary

        segment = text[start:end].strip()
        if segment:
            chunks.append((segment, start, end))

        if end >= text_len:
            break

        # Next window starts with overlap
        next_start = end - overlap
        # Snap to a word boundary going forward
        ws = text.find(" ", next_start)
        start = ws + 1 if ws != -1 and ws < end else end

    return chunks
