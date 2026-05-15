"""
Tests for POST /ask, POST /ask/local, POST /ask/online, POST /ask/{id}/approve.
Guardian and Expert are mocked throughout.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.guardian.pipeline import PipelineResult, PendingApproval
from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision


def _pipeline_result(**kwargs) -> PipelineResult:
    defaults = dict(
        answer="Some answer.",
        routing=RoutingDecision.GUARDED_ONLINE,
        privacy_level=PrivacyLevel.PUBLIC,
        local_sufficiency=LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY,
        sources=[],
        query_id="q-default",
    )
    defaults.update(kwargs)
    return PipelineResult(**defaults)


# ---------------------------------------------------------------------------
# POST /ask — auto-route
# ---------------------------------------------------------------------------

def test_ask_returns_ok(client):
    mock_result = _pipeline_result(
        answer="Python is a language.",
        routing=RoutingDecision.GUARDED_ONLINE,
        privacy_level=PrivacyLevel.PUBLIC,
        sources=["local", "expert"],
        query_id="q1",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "What is Python?"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["answer"] == "Python is a language."
    assert data["routing"] == "guarded-online"
    assert data["answer_mode"] == "Guarded online"
    assert "expert" in data["sources"]


def test_ask_blocked_query(client):
    mock_result = _pipeline_result(
        answer="Blocked.",
        routing=RoutingDecision.BLOCKED,
        privacy_level=PrivacyLevel.SECRET,
        local_sufficiency=LocalSufficiency.LOCAL_PRIVATE_BLOCKED,
        sources=[],
        query_id="q2",
        status="blocked",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "my secret password"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"
    assert resp.json()["answer_mode"] == "Blocked for privacy"


def test_ask_pending_approval(client):
    mock_result = PendingApproval(query_id="q3", preview={"sanitized_context": ""})
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "my medical records"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending_approval"
    assert data["query_id"] == "q3"
    assert data["answer"] is None


def test_ask_includes_warning_when_guardian_offline(client):
    mock_result = _pipeline_result(
        answer="Local answer.",
        routing=RoutingDecision.LOCAL_ONLY,
        privacy_level=PrivacyLevel.PRIVATE,
        local_sufficiency=LocalSufficiency.LOCAL_PRIVATE_BLOCKED,
        sources=["local"],
        query_id="q4",
        warning="Guardian offline — local-only mode",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "anything"})

    assert resp.status_code == 200
    assert "offline" in resp.json()["warning"].lower()


def test_ask_hybrid_routing(client):
    mock_result = _pipeline_result(
        answer="Hybrid answer.",
        routing=RoutingDecision.HYBRID_KNOWLEDGE_ONLY,
        privacy_level=PrivacyLevel.PRIVATE,
        local_sufficiency=LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL,
        sources=["local", "expert"],
        query_id="q-hybrid",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "career advice"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["routing"] == "hybrid-knowledge-only"
    assert data["answer_mode"] == "Hybrid"


def test_ask_returns_routing_detail(client):
    mock_result = _pipeline_result(
        answer="Answer.",
        routing=RoutingDecision.GUARDED_ONLINE,
        privacy_level=PrivacyLevel.PUBLIC,
        sources=["local", "expert"],
        query_id="q-detail",
        routing_detail={
            "route": "guarded-online",
            "needs_local_retrieval": False,
            "needs_online_model": True,
            "local_sufficiency": "LOCAL_MISSING_EXTERNAL_ONLY",
            "privacy_level": "PUBLIC",
            "reason": "test",
            "retrieved_sources": ["local", "expert"],
            "redaction_required": False,
            "approval_required": False,
        },
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask", json={"query": "What is Python?"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["routing_detail"] is not None
    assert data["routing_detail"]["route"] == "guarded-online"


# ---------------------------------------------------------------------------
# POST /ask/local
# ---------------------------------------------------------------------------

def test_ask_local_forces_local_only(client):
    mock_result = _pipeline_result(
        answer="Local only answer.",
        routing=RoutingDecision.LOCAL_ONLY,
        privacy_level=PrivacyLevel.PRIVATE,
        local_sufficiency=LocalSufficiency.LOCAL_SUFFICIENT,
        sources=["local"],
        query_id="q5",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask/local", json={"query": "private question"})

    assert resp.status_code == 200
    assert resp.json()["routing"] == "local-only"
    assert resp.json()["answer_mode"] == "Local only"
    # Confirm force_route=LOCAL_ONLY was passed
    call_kwargs = MockPipeline.return_value.run.call_args
    assert call_kwargs.kwargs.get("force_route") == RoutingDecision.LOCAL_ONLY


# ---------------------------------------------------------------------------
# POST /ask/online
# ---------------------------------------------------------------------------

def test_ask_online_forces_guarded_online(client):
    mock_result = _pipeline_result(
        answer="Online answer.",
        routing=RoutingDecision.GUARDED_ONLINE,
        privacy_level=PrivacyLevel.PUBLIC,
        sources=["local", "expert"],
        query_id="q6",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.run.return_value = mock_result
        resp = client.post("/ask/online", json={"query": "public question"})

    assert resp.status_code == 200
    call_kwargs = MockPipeline.return_value.run.call_args
    assert call_kwargs.kwargs.get("force_route") == RoutingDecision.GUARDED_ONLINE


# ---------------------------------------------------------------------------
# POST /ask/{id}/approve
# ---------------------------------------------------------------------------

def test_approve_resumes_pipeline(client):
    mock_result = _pipeline_result(
        answer="Approved answer.",
        routing=RoutingDecision.GUARDED_ONLINE,
        privacy_level=PrivacyLevel.HIGHLY_PRIVATE,
        local_sufficiency=LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL,
        sources=["local", "expert"],
        query_id="q7",
    )
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.resume.return_value = mock_result
        resp = client.post("/ask/q7/approve", json={"query": "my medical question"})

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Approved answer."
    MockPipeline.return_value.resume.assert_called_once_with("q7", "my medical question")


def test_approve_raises_404_on_error(client):
    with patch("app.routes.ask.Pipeline") as MockPipeline:
        MockPipeline.return_value.resume.side_effect = Exception("not found")
        resp = client.post("/ask/bad-id/approve", json={"query": "whatever"})
    assert resp.status_code == 404
