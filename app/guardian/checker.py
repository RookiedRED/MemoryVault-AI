from app.privacy.markers import has_private_markers
from app.guardian.sanitizer import RedactionMap


def check(response: str, redaction_map: RedactionMap | None) -> bool:
    """
    Check an Expert Model response for privacy leakage.

    Returns True  → response is clean, safe to use.
    Returns False → leakage detected; caller must fall back to local-only.

    Two checks:
      1. Generic PII scan (always runs) — catches raw credentials, SSNs, etc.
      2. Entity-specific check (runs if redaction_map is available) — verifies
         the Expert did not reconstruct any entity the sanitizer replaced with
         a placeholder token.

    If redaction_map is None (lost on crash between steps 5-8, per D5),
    only the generic scan runs.

    Note: forbidden_actions in the payload are advisory instructions to the Expert.
    THIS function is the actual enforcement gate.
    """
    # Check 1: generic PII scan
    if has_private_markers(response):
        return False

    # Check 2: entity-specific — did the Expert reconstruct a redacted value?
    if redaction_map:
        response_lower = response.lower()
        for _placeholder, original in redaction_map.items():
            if original.lower() in response_lower:
                return False

    return True
