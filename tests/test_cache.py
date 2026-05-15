"""
Tests for the Guardian classification cache.
"""

import time
from unittest.mock import MagicMock, patch

import app.guardian.cache as cache
from app.guardian.classifier import classify
from app.privacy.taxonomy import PrivacyLevel


def setup_function():
    cache.clear()


# ---------------------------------------------------------------------------
# Cache internals
# ---------------------------------------------------------------------------

def test_cache_miss_returns_none():
    assert cache.get("unknown query", "no context") is None


def test_cache_put_and_hit():
    cache.put("hello", "ctx", PrivacyLevel.PUBLIC, 0.9)
    result = cache.get("hello", "ctx")
    assert result == (PrivacyLevel.PUBLIC, 0.9)


def test_cache_different_queries_dont_collide():
    cache.put("query A", "", PrivacyLevel.PUBLIC, 0.9)
    cache.put("query B", "", PrivacyLevel.SECRET, 0.99)
    assert cache.get("query A", "")[0] == PrivacyLevel.PUBLIC
    assert cache.get("query B", "")[0] == PrivacyLevel.SECRET


def test_cache_expires_after_ttl(monkeypatch):
    cache.put("expiring", "", PrivacyLevel.PUBLIC, 0.8)
    # Fast-forward past TTL
    monkeypatch.setattr("app.guardian.cache._TTL_SECONDS", -1)
    cache.put("expiring2", "", PrivacyLevel.PRIVATE, 0.7)
    assert cache.get("expiring2", "") is None


def test_cache_clear():
    cache.put("a", "", PrivacyLevel.PUBLIC, 0.5)
    cache.clear()
    assert cache.size() == 0


# ---------------------------------------------------------------------------
# Classify uses cache
# ---------------------------------------------------------------------------

def test_classify_caches_result():
    mock_guardian = MagicMock()
    mock_guardian.generate.return_value = '{"privacy_level": "PUBLIC", "confidence": 0.9, "reason": "test"}'

    classify("What is Python?", "", mock_guardian)
    classify("What is Python?", "", mock_guardian)  # second call

    # Guardian should only be called once — second call is a cache hit
    assert mock_guardian.generate.call_count == 1


def test_classify_skips_cache_on_different_query():
    mock_guardian = MagicMock()
    mock_guardian.generate.return_value = '{"privacy_level": "PUBLIC", "confidence": 0.9, "reason": "test"}'

    classify("query one", "", mock_guardian)
    classify("query two", "", mock_guardian)

    assert mock_guardian.generate.call_count == 2
