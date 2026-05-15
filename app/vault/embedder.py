"""
Embedder — lazy singleton wrapping sentence-transformers BGE model.

The model is loaded on first use so startup and import are always fast.
Call embed() or embed_one() to get normalised float vectors.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from app.config import EMBEDDING_DIM, EMBEDDING_MODEL

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None


def _get_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """
    Return a list of normalised embedding vectors (one per input text).
    Each vector has EMBEDDING_DIM dimensions.
    """
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def embed_one(text: str) -> list[float]:
    """Convenience wrapper for a single text."""
    return embed([text])[0]


def serialize(vector: list[float]) -> bytes:
    """Pack a float list into the binary format expected by sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)
