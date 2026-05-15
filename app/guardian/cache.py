"""
Classification cache — short-lived TTL cache for Guardian analyze() results.

Identical (query, context_snippet) pairs within the TTL window skip the
Ollama call entirely. The cache is in-process and never persisted.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.guardian.classifier import AnalysisResult

_TTL_SECONDS: int = 300  # 5 minutes

# key -> (AnalysisResult, expires_at)
_store: dict[str, tuple["AnalysisResult", float]] = {}


def _key(query: str, context_snippet: str) -> str:
    raw = f"{query}|||{context_snippet[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get(query: str, context_snippet: str) -> Optional["AnalysisResult"]:
    k = _key(query, context_snippet)
    entry = _store.get(k)
    if entry is None:
        return None
    result, expires_at = entry
    if time.time() > expires_at:
        del _store[k]
        return None
    return result


def put(query: str, context_snippet: str, result: "AnalysisResult") -> None:
    k = _key(query, context_snippet)
    _store[k] = (result, time.time() + _TTL_SECONDS)


def clear() -> None:
    """Clear all cached entries (useful in tests)."""
    _store.clear()


def size() -> int:
    return len(_store)
