"""
Tests for the Guardian Pipeline — all routing paths, with Guardian and Expert mocked.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.guardian.pipeline import Pipeline, PipelineResult, PendingApproval
from app.guardian.sanitizer import BlockedError
from app.privacy.taxonomy import PrivacyLevel, RoutingDecision


@pytest.fixture
def mock_guardian():
    g = MagicMock()
    g.is_available.return_value = True
    g.generate.return_value = "A clear and concise answer."
    return g


@pytest.fixture
def pipeline(mock_guardian, tmp_db):
    return Pipeline(guardian=mock_guardian, db_path=tmp_db)


# ---------------------------------------------------------------------------
# Guardian unavailable — D11: local-only + warning
# ---------------------------------------------------------------------------

def test_guardian_unavailable_returns_local_answer(tmp_db):
    g = MagicMock()
    g.is_available.return_value = False
    p = Pipeline(guardian=g, db_path=tmp_db)
    result = p.run("What is Python?")
    assert isinstance(result, PipelineResult)
    assert result.routing == RoutingDecision.LOCAL_ONLY
    assert result.warning is not None
    assert "offline" in result.warning.lower()


def test_guardian_unavailable_zero_network_entries(tmp_db):
    from app.database import get_connection
    g = MagicMock()
    g.is_available.return_value = False
    p = Pipeline(guardian=g, db_path=tmp_db)
    p.run("Any query")
    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Force local-only path
# ---------------------------------------------------------------------------

def test_force_local_only_no_expert_call(pipeline, mock_guardian):
    with patch.object(pipeline, '_get_expert') as mock_expert:
        result = pipeline.run("Question", force_route=RoutingDecision.LOCAL_ONLY)
    assert isinstance(result, PipelineResult)
    assert result.routing == RoutingDecision.LOCAL_ONLY
    assert "expert" not in result.sources
    mock_expert.assert_not_called()


def test_local_only_zero_network_entries(pipeline, tmp_db):
    from app.database import get_connection
    with patch.object(pipeline, '_get_expert'):
        pipeline.run("Local question", force_route=RoutingDecision.LOCAL_ONLY)
    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Blocked path — SECRET data
# ---------------------------------------------------------------------------

def test_secret_query_returns_blocked(pipeline, mock_guardian):
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.SECRET, 0.95)):
        result = pipeline.run("password: abc123")
    assert isinstance(result, PipelineResult)
    assert result.status == "blocked"
    assert result.routing == RoutingDecision.BLOCKED


def test_blocked_zero_network_entries(pipeline, tmp_db):
    from app.database import get_connection
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.SECRET, 0.95)):
        pipeline.run("secret query")
    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Approval-required path
# ---------------------------------------------------------------------------

def test_highly_private_returns_pending(pipeline):
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.HIGHLY_PRIVATE, 0.9)):
        result = pipeline.run("My doctor said I have diabetes")
    assert isinstance(result, PendingApproval)
    assert result.status == "pending_approval"
    assert result.query_id is not None


def test_approval_required_no_expert_call(pipeline, tmp_db):
    from app.database import get_connection
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.HIGHLY_PRIVATE, 0.9)), \
         patch.object(pipeline, '_get_expert') as mock_expert:
        pipeline.run("private medical query")
    mock_expert.assert_not_called()
    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 0


def test_resume_calls_expert(pipeline, tmp_db):
    from app.database import get_connection
    import time

    # Pre-insert a query record so the pipeline can log the classification
    with get_connection(tmp_db) as conn:
        conn.execute(
            "INSERT INTO queries (id, query_text, routing_decision, created_at) VALUES (?, ?, ?, ?)",
            ("qid-1", "prior query", "guarded-online", time.time()),
        )
        conn.commit()

    mock_expert = MagicMock()
    mock_expert.call_with_usage.return_value = ("Expert answer.", 100, 50)
    with patch.object(pipeline, '_get_expert', return_value=mock_expert), \
         patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)):
        result = pipeline.resume("qid-1", "My question after approval")

    assert isinstance(result, PipelineResult)
    mock_expert.call_with_usage.assert_called_once()


# ---------------------------------------------------------------------------
# Guarded-online path — happy path
# ---------------------------------------------------------------------------

def test_guarded_online_expert_called(pipeline):
    mock_expert = MagicMock()
    mock_expert.call_with_usage.return_value = ("Expert answer about Python.", 100, 50)
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)), \
         patch.object(pipeline, '_get_expert', return_value=mock_expert):
        result = pipeline.run("What is Python?")
    assert isinstance(result, PipelineResult)
    mock_expert.call_with_usage.assert_called_once()
    assert "expert" in result.sources


def test_guarded_online_audit_entry_created(pipeline, tmp_db):
    from app.database import get_connection
    mock_expert = MagicMock()
    mock_expert.call_with_usage.return_value = ("General answer.", 100, 50)
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)), \
         patch.object(pipeline, '_get_expert', return_value=mock_expert):
        pipeline.run("Public question")
    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# Leak fallback — D8
# ---------------------------------------------------------------------------

def test_leak_detected_falls_back_to_local(pipeline, tmp_db):
    from app.database import get_connection
    mock_expert = MagicMock()
    mock_expert.call_with_usage.return_value = ("Response leaking John Smith's password: hunter2", 100, 50)
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)), \
         patch.object(pipeline, '_get_expert', return_value=mock_expert):
        result = pipeline.run("Tell me about security")
    assert result.routing == RoutingDecision.LOCAL_ONLY
    # Audit entry exists and has leak_detected=1
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT leak_detected FROM network_audit_log").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1


# ---------------------------------------------------------------------------
# Expert API failure fallback
# ---------------------------------------------------------------------------

def test_expert_unavailable_falls_back_to_local(pipeline):
    mock_expert = MagicMock()
    mock_expert.call_with_usage.side_effect = Exception("OpenAI API error")
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)), \
         patch.object(pipeline, '_get_expert', return_value=mock_expert):
        result = pipeline.run("Public question")
    assert result.routing == RoutingDecision.LOCAL_ONLY


# ---------------------------------------------------------------------------
# final_answer_checked_locally invariant
# ---------------------------------------------------------------------------

def test_finalize_always_called_on_online_path(pipeline, mock_guardian):
    mock_expert = MagicMock()
    mock_expert.call_with_usage.return_value = ("Clean expert answer.", 100, 50)
    with patch('app.guardian.pipeline.classify', return_value=(PrivacyLevel.PUBLIC, 0.9)), \
         patch.object(pipeline, '_get_expert', return_value=mock_expert):
        result = pipeline.run("Question")
    # Guardian.generate was called at least once (finalize step 10)
    mock_guardian.generate.assert_called()
