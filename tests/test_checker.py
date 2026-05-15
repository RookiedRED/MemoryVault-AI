"""
Tests for ResponseChecker: leak detection, clean responses, redaction map matching.
"""

from app.guardian.checker import check


# ---------------------------------------------------------------------------
# Clean responses
# ---------------------------------------------------------------------------

def test_clean_response_returns_true():
    assert check("Python is a great language for data science.", {}) is True


def test_clean_response_no_redaction_map():
    assert check("Here is a general answer about career planning.", None) is True


def test_clean_placeholder_in_response_not_flagged():
    # The Expert echoing a placeholder back is not a leak
    assert check("I will refer to the person as [PERSON_1] in my answer.", {"[PERSON_1]": "John Smith"}) is True


# ---------------------------------------------------------------------------
# Leak detection — entity-specific
# ---------------------------------------------------------------------------

def test_reconstructed_name_detected():
    rmap = {"[PERSON_1]": "John Smith"}
    assert check("The answer involves John Smith directly.", rmap) is False


def test_reconstructed_email_detected():
    rmap = {"[EMAIL_1]": "alice@corp.com"}
    assert check("You can reach alice@corp.com for more details.", rmap) is False


def test_reconstructed_phone_detected():
    rmap = {"[PHONE_1]": "555-867-5309"}
    assert check("The number 555-867-5309 was mentioned in context.", rmap) is False


def test_case_insensitive_match():
    rmap = {"[PERSON_1]": "Alice Johnson"}
    assert check("alice johnson is the contact person.", rmap) is False


# ---------------------------------------------------------------------------
# Leak detection — generic PII scan
# ---------------------------------------------------------------------------

def test_raw_password_in_response_detected():
    assert check("Here is the info: password: hunter2", None) is False


def test_raw_api_key_in_response_detected():
    assert check("Use this key: sk-" + "a" * 48, {}) is False


def test_ssn_in_response_detected():
    assert check("The SSN on file is 123-45-6789.", None) is False


# ---------------------------------------------------------------------------
# Fallback when redaction map is None (crash scenario)
# ---------------------------------------------------------------------------

def test_none_map_falls_back_to_generic_scan_clean():
    assert check("General advice about your career path.", None) is True


def test_none_map_falls_back_to_generic_scan_detects_credential():
    assert check("api_key=sk-abc123def456ghi789jkl012mno345pqr", None) is False


# ---------------------------------------------------------------------------
# Empty map
# ---------------------------------------------------------------------------

def test_empty_map_clean_response():
    assert check("Here is the answer.", {}) is True
