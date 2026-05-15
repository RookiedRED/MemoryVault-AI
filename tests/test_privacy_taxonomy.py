"""
Tests for the privacy taxonomy: PrivacyLevel enum, RoutingDecision enum,
default routing table, and the has_private_markers gate.
"""

import pytest

from app.privacy.markers import has_private_markers
from app.privacy.taxonomy import PrivacyLevel, RoutingDecision, default_route


# ---------------------------------------------------------------------------
# PrivacyLevel enum
# ---------------------------------------------------------------------------

def test_all_levels_defined():
    levels = {l.value for l in PrivacyLevel}
    assert levels == {"PUBLIC", "LOW_SENSITIVE", "PRIVATE", "HIGHLY_PRIVATE", "SECRET"}


def test_privacy_level_from_string():
    assert PrivacyLevel("PUBLIC") is PrivacyLevel.PUBLIC
    assert PrivacyLevel("SECRET") is PrivacyLevel.SECRET


def test_privacy_level_invalid_raises():
    with pytest.raises(ValueError):
        PrivacyLevel("UNKNOWN")


# ---------------------------------------------------------------------------
# RoutingDecision enum
# ---------------------------------------------------------------------------

def test_all_routing_decisions_defined():
    decisions = {d.value for d in RoutingDecision}
    assert decisions == {"local-only", "guarded-online", "approval-required", "blocked"}


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

def test_public_routes_to_guarded_online():
    assert default_route(PrivacyLevel.PUBLIC) == RoutingDecision.GUARDED_ONLINE


def test_low_sensitive_routes_to_guarded_online():
    assert default_route(PrivacyLevel.LOW_SENSITIVE) == RoutingDecision.GUARDED_ONLINE


def test_private_routes_to_guarded_online():
    assert default_route(PrivacyLevel.PRIVATE) == RoutingDecision.GUARDED_ONLINE


def test_highly_private_routes_to_approval_required():
    assert default_route(PrivacyLevel.HIGHLY_PRIVATE) == RoutingDecision.APPROVAL_REQUIRED


def test_secret_routes_to_blocked():
    assert default_route(PrivacyLevel.SECRET) == RoutingDecision.BLOCKED


def test_secret_never_guarded_online():
    assert default_route(PrivacyLevel.SECRET) != RoutingDecision.GUARDED_ONLINE


# ---------------------------------------------------------------------------
# has_private_markers — hard gate
# ---------------------------------------------------------------------------

def test_clean_text_has_no_markers():
    assert not has_private_markers("What is the capital of France?")


def test_password_field_detected():
    assert has_private_markers("password: hunter2")


def test_api_key_detected():
    assert has_private_markers("api_key=sk-abc123def456ghi789jkl012mno345pqr")


def test_bearer_token_detected():
    assert has_private_markers("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.xyz")


def test_ssn_detected():
    assert has_private_markers("My SSN is 123-45-6789")


def test_openai_key_detected():
    assert has_private_markers("sk-" + "a" * 48)


def test_placeholder_tokens_not_flagged():
    # After sanitization, placeholders like [PERSON_1] must not trigger the gate
    assert not has_private_markers("The report was written by [PERSON_1] at [ADDRESS_1].")


def test_mixed_placeholder_and_clean_text():
    assert not has_private_markers(
        "Contact [PERSON_1] at [EMAIL_1] for more information about the project."
    )


def test_secret_phrase_in_context():
    # "secret:" prefix in key-value form
    assert has_private_markers("secret: my_super_secret_value")
