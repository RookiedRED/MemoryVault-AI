"""
Tests for the privacy taxonomy: PrivacyLevel enum, LocalSufficiency enum,
RoutingDecision enum, default_route(), and the has_private_markers gate.
"""

import pytest

from app.privacy.markers import has_private_markers
from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision, default_route


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
# LocalSufficiency enum
# ---------------------------------------------------------------------------

def test_all_local_sufficiency_values_defined():
    values = {s.value for s in LocalSufficiency}
    assert values == {
        "LOCAL_SUFFICIENT",
        "LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL",
        "LOCAL_MISSING_EXTERNAL_ONLY",
        "LOCAL_PRIVATE_BLOCKED",
    }


def test_local_sufficiency_from_string():
    assert LocalSufficiency("LOCAL_SUFFICIENT") is LocalSufficiency.LOCAL_SUFFICIENT
    assert LocalSufficiency("LOCAL_PRIVATE_BLOCKED") is LocalSufficiency.LOCAL_PRIVATE_BLOCKED


def test_local_sufficiency_invalid_raises():
    with pytest.raises(ValueError):
        LocalSufficiency("UNKNOWN")


# ---------------------------------------------------------------------------
# RoutingDecision enum
# ---------------------------------------------------------------------------

def test_all_routing_decisions_defined():
    decisions = {d.value for d in RoutingDecision}
    assert decisions == {"local-only", "guarded-online", "hybrid-knowledge-only", "approval-required", "blocked"}


def test_hybrid_knowledge_only_value():
    assert RoutingDecision.HYBRID_KNOWLEDGE_ONLY.value == "hybrid-knowledge-only"


# ---------------------------------------------------------------------------
# Routing table — default_route(level, sufficiency)
# ---------------------------------------------------------------------------

def test_secret_always_blocked():
    for suf in LocalSufficiency:
        assert default_route(PrivacyLevel.SECRET, suf) == RoutingDecision.BLOCKED


def test_local_sufficient_always_local_only():
    for level in [PrivacyLevel.PUBLIC, PrivacyLevel.LOW_SENSITIVE, PrivacyLevel.PRIVATE, PrivacyLevel.HIGHLY_PRIVATE]:
        assert default_route(level, LocalSufficiency.LOCAL_SUFFICIENT) == RoutingDecision.LOCAL_ONLY


def test_local_private_blocked_always_local_only():
    for level in [PrivacyLevel.PUBLIC, PrivacyLevel.LOW_SENSITIVE, PrivacyLevel.PRIVATE, PrivacyLevel.HIGHLY_PRIVATE]:
        assert default_route(level, LocalSufficiency.LOCAL_PRIVATE_BLOCKED) == RoutingDecision.LOCAL_ONLY


def test_local_missing_external_only_always_guarded_online():
    for level in [PrivacyLevel.PUBLIC, PrivacyLevel.LOW_SENSITIVE, PrivacyLevel.PRIVATE, PrivacyLevel.HIGHLY_PRIVATE]:
        assert default_route(level, LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY) == RoutingDecision.GUARDED_ONLINE


def test_public_insufficient_routes_guarded_online():
    assert default_route(PrivacyLevel.PUBLIC, LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL) == RoutingDecision.GUARDED_ONLINE


def test_low_sensitive_insufficient_routes_guarded_online():
    assert default_route(PrivacyLevel.LOW_SENSITIVE, LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL) == RoutingDecision.GUARDED_ONLINE


def test_private_insufficient_routes_hybrid():
    assert default_route(PrivacyLevel.PRIVATE, LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL) == RoutingDecision.HYBRID_KNOWLEDGE_ONLY


def test_highly_private_insufficient_routes_approval_required():
    assert default_route(PrivacyLevel.HIGHLY_PRIVATE, LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL) == RoutingDecision.APPROVAL_REQUIRED


def test_secret_never_guarded_online():
    for suf in LocalSufficiency:
        assert default_route(PrivacyLevel.SECRET, suf) != RoutingDecision.GUARDED_ONLINE


# ---------------------------------------------------------------------------
# Backwards compatibility helpers — these used to be the old routing table tests
# ---------------------------------------------------------------------------

def test_public_routes_to_guarded_online():
    assert default_route(PrivacyLevel.PUBLIC, LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY) == RoutingDecision.GUARDED_ONLINE


def test_low_sensitive_routes_to_guarded_online():
    assert default_route(PrivacyLevel.LOW_SENSITIVE, LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY) == RoutingDecision.GUARDED_ONLINE


def test_highly_private_routes_to_approval_required():
    assert default_route(PrivacyLevel.HIGHLY_PRIVATE, LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL) == RoutingDecision.APPROVAL_REQUIRED


def test_secret_routes_to_blocked():
    assert default_route(PrivacyLevel.SECRET, LocalSufficiency.LOCAL_SUFFICIENT) == RoutingDecision.BLOCKED


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
