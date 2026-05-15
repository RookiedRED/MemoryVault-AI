import re

# Patterns that indicate raw sensitive data that must NEVER be sent online.
# Checked as a hard gate (assert not has_private_markers(payload)) before every Expert call.
_BLACKLIST_PATTERNS: list[re.Pattern] = [
    re.compile(r'password\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'api[_\s-]?key\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'secret\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'private[_\s-]?key\b', re.IGNORECASE),
    re.compile(r'auth[_\s-]?token\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'bearer\s+[A-Za-z0-9\-_\.]+', re.IGNORECASE),
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),          # SSN
    re.compile(r'\b(?:\d{4}[\s\-]?){3}\d{4}\b'),   # credit card
    re.compile(r'\bsk-[A-Za-z0-9]{20,}\b'),         # OpenAI-style keys
    re.compile(r'\bghp_[A-Za-z0-9]{36}\b'),         # GitHub personal access tokens
]

# Placeholder patterns produced by the sanitizer — these are expected in sanitized text
# and must NOT be treated as private markers.
PLACEHOLDER_RE = re.compile(r'\[(?:PERSON|ADDRESS|EMAIL|PHONE|ID|SECRET|ENTITY)_\d+\]')


def has_private_markers(text: str) -> bool:
    """
    Returns True if `text` contains raw sensitive patterns that must never be sent online.

    This is the hard gate. Called as:
        assert not has_private_markers(payload)
    before every ExpertModelClient.call(). A failing assert means the sanitizer
    missed something — it surfaces the bug at dev time, not as a production data leak.

    Does NOT flag placeholder tokens ([PERSON_1], [EMAIL_2], etc.) — those are the
    sanitizer's output and are expected in clean payloads.
    """
    # Strip placeholders before checking, so we don't false-positive on the sanitizer's output
    stripped = PLACEHOLDER_RE.sub('', text)
    for pattern in _BLACKLIST_PATTERNS:
        if pattern.search(stripped):
            return True
    return False
