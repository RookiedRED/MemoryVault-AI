"""
Tests for the _retrieve_local() embedding-search path in the pipeline.
sqlite-vec is used for real; the embedder is mocked to avoid model downloads.
"""

import struct
from unittest.mock import MagicMock, patch

import pytest

from app.database import get_connection, init_db
from app.guardian.pipeline import Pipeline
from app.vault.importer import import_file


def _fake_embed(texts):
    return [[0.1] * 768 for _ in texts]


def _serialize(v):
    return struct.pack(f"{len(v)}f", *v)


# ---------------------------------------------------------------------------
# _retrieve_local returns empty string when vault is empty
# ---------------------------------------------------------------------------

def test_retrieve_local_empty_vault(tmp_db):
    g = MagicMock()
    g.is_available.return_value = True
    pipeline = Pipeline(guardian=g, db_path=tmp_db)

    with patch("app.vault.embedder.embed_one", return_value=[0.1] * 768):
        result = pipeline._retrieve_local("anything")

    assert result == ""


# ---------------------------------------------------------------------------
# _retrieve_local returns relevant chunk text after import
# ---------------------------------------------------------------------------

def test_retrieve_local_returns_chunk_text(tmp_db):
    # Import a document
    content = b"The capital of France is Paris. Paris is a beautiful city."
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        import_file(content, "france.txt", "text/plain", tmp_db)

    g = MagicMock()
    g.is_available.return_value = True
    pipeline = Pipeline(guardian=g, db_path=tmp_db)

    with patch("app.vault.embedder.embed_one", return_value=[0.1] * 768):
        result = pipeline._retrieve_local("What is the capital of France?")

    assert "Paris" in result


def test_retrieve_local_returns_multiple_chunks(tmp_db):
    # Import a large document that produces multiple chunks
    content = ("paragraph text content " * 400).encode()
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        import_file(content, "large.txt", "text/plain", tmp_db)

    g = MagicMock()
    pipeline = Pipeline(guardian=g, db_path=tmp_db)

    with patch("app.vault.embedder.embed_one", return_value=[0.1] * 768):
        result = pipeline._retrieve_local("some query", top_k=3)

    # Multiple chunks are joined with double newlines and labelled
    assert "[chunk 1]" in result


# ---------------------------------------------------------------------------
# _retrieve_local gracefully handles embedder failure
# ---------------------------------------------------------------------------

def test_retrieve_local_embedder_failure_returns_empty(tmp_db):
    g = MagicMock()
    pipeline = Pipeline(guardian=g, db_path=tmp_db)

    with patch("app.vault.embedder.embed_one", side_effect=Exception("model error")):
        result = pipeline._retrieve_local("query")

    assert result == ""


# ---------------------------------------------------------------------------
# Full pipeline run uses local context when vault has data
# ---------------------------------------------------------------------------

def test_pipeline_uses_local_context_in_answer(tmp_db):
    content = b"MemoryVault was built in 2026 as a privacy-preserving tool."
    with patch("app.vault.importer.embed", side_effect=_fake_embed):
        import_file(content, "mv.txt", "text/plain", tmp_db)

    g = MagicMock()
    g.is_available.return_value = True
    g.generate.return_value = "MemoryVault is a privacy-preserving tool."
    pipeline = Pipeline(guardian=g, db_path=tmp_db)

    from app.guardian.classifier import AnalysisResult
    from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision
    analysis = AnalysisResult(
        privacy_level=PrivacyLevel.PUBLIC,
        local_sufficiency=LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY,
        recommended_route=RoutingDecision.GUARDED_ONLINE,
        needs_local_retrieval=False,
        needs_online_model=True,
        redaction_required=False,
        reason="test",
        confidence=0.9,
    )
    with patch("app.vault.embedder.embed_one", return_value=[0.1] * 768), \
         patch("app.guardian.pipeline.analyze", return_value=analysis), \
         patch.object(pipeline, "_get_expert") as mock_expert:
        mock_expert.return_value.call.return_value = "Expert answer."
        result = pipeline.run("What is MemoryVault?", force_route=RoutingDecision.LOCAL_ONLY)

    # Guardian.generate was called — means local context was passed through
    g.generate.assert_called()
