"""
Tests for PayloadSanitizer: redaction map, assert gate, BlockedError, placeholder substitution.
"""

import pytest
from unittest.mock import MagicMock

from app.guardian.sanitizer import BlockedError, sanitize, SanitizedPayload
from app.privacy.markers import has_private_markers
from app.privacy.taxonomy import PrivacyLevel


@pytest.fixture
def mock_guardian():
    g = MagicMock()
    g.generate.return_value = "An anonymized summary of the career notes."
    return g


# ---------------------------------------------------------------------------
# PUBLIC level — context passes through unchanged
# ---------------------------------------------------------------------------

def test_public_context_passes_through(mock_guardian):
    payload, rmap = sanitize("What is Python?", "Python is a programming language.", PrivacyLevel.PUBLIC, mock_guardian)
    assert "Python" in payload.sanitized_context
    assert rmap == {}


def test_public_payload_has_forbidden_actions(mock_guardian):
    payload, _ = sanitize("question", "context", PrivacyLevel.PUBLIC, mock_guardian)
    assert len(payload.forbidden_actions) >= 4
    assert any("identity" in a for a in payload.forbidden_actions)


# ---------------------------------------------------------------------------
# LOW_SENSITIVE level — PII redacted
# ---------------------------------------------------------------------------

def test_email_redacted(mock_guardian):
    ctx = "Contact me at john.doe@example.com for details."
    payload, rmap = sanitize("how to reach?", ctx, PrivacyLevel.LOW_SENSITIVE, mock_guardian)
    assert "john.doe@example.com" not in payload.sanitized_context
    assert "[EMAIL_1]" in payload.sanitized_context
    assert rmap["[EMAIL_1]"] == "john.doe@example.com"


def test_phone_redacted(mock_guardian):
    ctx = "Call me at 555-867-5309 anytime."
    payload, rmap = sanitize("contact info", ctx, PrivacyLevel.LOW_SENSITIVE, mock_guardian)
    assert "867-5309" not in payload.sanitized_context
    assert any("PHONE" in k for k in rmap)


def test_redaction_map_placeholder_to_original(mock_guardian):
    ctx = "Email: alice@corp.com"
    _, rmap = sanitize("contact", ctx, PrivacyLevel.LOW_SENSITIVE, mock_guardian)
    # map is placeholder → original
    assert any(v == "alice@corp.com" for v in rmap.values())


# ---------------------------------------------------------------------------
# PRIVATE level — Guardian anonymizes
# ---------------------------------------------------------------------------

def test_private_calls_guardian_anonymize(mock_guardian):
    sanitize("career advice", "I work at Acme Corp as VP of Sales.", PrivacyLevel.PRIVATE, mock_guardian)
    mock_guardian.generate.assert_called_once()


def test_private_sanitized_context_is_guardian_output(mock_guardian):
    payload, _ = sanitize("career advice", "I work at Acme Corp.", PrivacyLevel.PRIVATE, mock_guardian)
    assert payload.sanitized_context == "An anonymized summary of the career notes."


# ---------------------------------------------------------------------------
# SECRET level — always blocked
# ---------------------------------------------------------------------------

def test_secret_raises_blocked_error(mock_guardian):
    with pytest.raises(BlockedError):
        sanitize("question", "context", PrivacyLevel.SECRET, mock_guardian)


# ---------------------------------------------------------------------------
# Blacklisted content — always blocked regardless of level
# ---------------------------------------------------------------------------

def test_password_in_context_raises_blocked(mock_guardian):
    with pytest.raises(BlockedError):
        sanitize("help", "password: abc123", PrivacyLevel.PUBLIC, mock_guardian)


def test_api_key_in_context_raises_blocked(mock_guardian):
    with pytest.raises(BlockedError):
        sanitize("help", "api_key=sk-" + "x" * 48, PrivacyLevel.LOW_SENSITIVE, mock_guardian)


# ---------------------------------------------------------------------------
# Assert gate — sanitized payload must have no private markers
# ---------------------------------------------------------------------------

def test_assert_gate_fires_on_remaining_markers(mock_guardian):
    """
    If the Guardian's anonymize returns text that still contains a raw credential,
    the assert gate must catch it.
    """
    mock_guardian.generate.return_value = "Contact me using password: stillhere123"
    with pytest.raises(AssertionError):
        sanitize("question", "some private context", PrivacyLevel.PRIVATE, mock_guardian)


# ---------------------------------------------------------------------------
# Payload structure
# ---------------------------------------------------------------------------

def test_payload_mode_is_guarded_online(mock_guardian):
    payload, _ = sanitize("q", "c", PrivacyLevel.PUBLIC, mock_guardian)
    assert payload.mode == "guarded_online"


def test_payload_privacy_level_matches(mock_guardian):
    payload, _ = sanitize("q", "c", PrivacyLevel.LOW_SENSITIVE, mock_guardian)
    assert payload.privacy_level == "LOW_SENSITIVE"


def test_payload_user_question_preserved(mock_guardian):
    payload, _ = sanitize("What should I do next?", "context", PrivacyLevel.PUBLIC, mock_guardian)
    assert payload.user_question == "What should I do next?"
