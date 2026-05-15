"""
Classification cache — short-lived TTL cache for Guardian classify() results.

Identical (query, context_snippet) pairs within the TTL window skip the
Ollama call entirely. The cache is in-process and never persisted.
"""

from __future__ import annotations

import hashlib
import time
from typing import Optional

from app.privacy.taxonomy import PrivacyLevel

_TTL_SECONDS: int = 300  # 5 minutes

# key -> (PrivacyLevel, confidence, expires_at)
_store: dict[str, tuple[PrivacyLevel, float, float]] = {}


def _key(query: str, context_snippet: str) -> str:
    raw = f"{query}|||{context_snippet[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get(query: str, context_snippet: str) -> Optional[tuple[PrivacyLevel, float]]:
    k = _key(query, context_snippet)
    entry = _store.get(k)
    if entry is None:
        return None
    level, confidence, expires_at = entry
    if time.time() > expires_at:
        del _store[k]
        return None
    return level, confidence


def put(query: str, context_snippet: str, level: PrivacyLevel, confidence: float) -> None:
    k = _key(query, context_snippet)
    _store[k] = (level, confidence, time.time() + _TTL_SECONDS)


def clear() -> None:
    """Clear all cached entries (useful in tests)."""
    _store.clear()


def size() -> int:
    return len(_store)
