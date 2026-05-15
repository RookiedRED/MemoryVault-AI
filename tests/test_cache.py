"""
Tests for the Guardian classification cache.
"""

import time
from unittest.mock import MagicMock, patch

import app.guardian.cache as cache
from app.guardian.classifier import AnalysisResult, analyze
from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision


def _make_result(level=PrivacyLevel.PUBLIC, sufficiency=LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY) -> AnalysisResult:
    return AnalysisResult(
        privacy_level=level,
        local_sufficiency=sufficiency,
        recommended_route=RoutingDecision.GUARDED_ONLINE,
        needs_local_retrieval=False,
        needs_online_model=True,
        redaction_required=False,
        reason="test",
        confidence=0.9,
    )


def setup_function():
    cache.clear()


# ---------------------------------------------------------------------------
# Cache internals
# ---------------------------------------------------------------------------

def test_cache_miss_returns_none():
    assert cache.get("unknown query", "no context") is None


def test_cache_put_and_hit():
    result = _make_result(PrivacyLevel.PUBLIC)
    cache.put("hello", "ctx", result)
    hit = cache.get("hello", "ctx")
    assert hit is not None
    assert hit.privacy_level == PrivacyLevel.PUBLIC
    assert hit.confidence == 0.9


def test_cache_different_queries_dont_collide():
    result_a = _make_result(PrivacyLevel.PUBLIC)
    result_b = _make_result(PrivacyLevel.SECRET)
    cache.put("query A", "", result_a)
    cache.put("query B", "", result_b)
    assert cache.get("query A", "").privacy_level == PrivacyLevel.PUBLIC
    assert cache.get("query B", "").privacy_level == PrivacyLevel.SECRET


def test_cache_expires_after_ttl(monkeypatch):
    result = _make_result(PrivacyLevel.PUBLIC)
    cache.put("expiring", "", result)
    # Fast-forward past TTL
    monkeypatch.setattr("app.guardian.cache._TTL_SECONDS", -1)
    result2 = _make_result(PrivacyLevel.PRIVATE)
    cache.put("expiring2", "", result2)
    assert cache.get("expiring2", "") is None


def test_cache_clear():
    result = _make_result()
    cache.put("a", "", result)
    cache.clear()
    assert cache.size() == 0


# ---------------------------------------------------------------------------
# Analyze uses cache
# ---------------------------------------------------------------------------

def test_analyze_caches_result():
    mock_guardian = MagicMock()
    mock_guardian.generate.return_value = (
        '{"privacy_level": "PUBLIC", "local_sufficiency": "LOCAL_MISSING_EXTERNAL_ONLY", '
        '"recommended_route": "guarded-online", "needs_local_retrieval": false, '
        '"needs_online_model": true, "redaction_required": false, '
        '"reason": "test", "confidence": 0.9}'
    )

    analyze("What is Python?", "", mock_guardian)
    analyze("What is Python?", "", mock_guardian)  # second call

    # Guardian should only be called once — second call is a cache hit
    assert mock_guardian.generate.call_count == 1


def test_analyze_skips_cache_on_different_query():
    mock_guardian = MagicMock()
    mock_guardian.generate.return_value = (
        '{"privacy_level": "PUBLIC", "local_sufficiency": "LOCAL_MISSING_EXTERNAL_ONLY", '
        '"recommended_route": "guarded-online", "needs_local_retrieval": false, '
        '"needs_online_model": true, "redaction_required": false, '
        '"reason": "test", "confidence": 0.9}'
    )

    analyze("query one", "", mock_guardian)
    analyze("query two", "", mock_guardian)

    assert mock_guardian.generate.call_count == 2
