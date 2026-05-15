import json
import re
from dataclasses import dataclass, field

from app.guardian.model import GuardianModel
from app.privacy.markers import has_private_markers
from app.privacy.taxonomy import PrivacyLevel


# ---------------------------------------------------------------------------
# Module-level prompt prefixes (Ollama KV-cache optimisation)
#
# The static part of each Guardian prompt is defined once here so that Ollama
# sees the same leading token sequence on every call and can reuse the cached
# KV state rather than recomputing it. Dynamic content (query, context) is
# always appended *after* the fixed prefix.
# ---------------------------------------------------------------------------

_ANONYMIZE_PREFIX = (
    "Anonymize the following text for external use.\n"
    "Remove all names, contact info, addresses, and any identifying details.\n"
    "Replace specifics with generic descriptions.\n"
    "Keep only information that is relevant to the user's question.\n\n"
)

_HYBRID_PAYLOAD_PREFIX = (
    "You are preparing a privacy-safe request for an online AI model.\n"
    "The user has private local context that must NOT be sent online.\n\n"
    "Given the original question and local context below, produce two things:\n"
    "1. An abstracted version of the question that removes all private details but preserves the general intent.\n"
    "2. A one-sentence topic summary of what local information is available (no actual content, just categories).\n\n"
    'Return ONLY valid JSON:\n{"abstracted_question": "...", "local_context_summary": "..."}\n\n'
)


class BlockedError(Exception):
    """Raised when content contains patterns that can never be sent online."""


@dataclass
class SanitizedPayload:
    mode: str = "guarded_online"
    route: str = ""
    task: str = ""
    privacy_level: str = ""
    user_question: str = ""
    sanitized_context: str = ""        # empty for hybrid route
    local_context_summary: str = ""    # brief topic summary, no raw data
    allowed_reasoning: list = field(default_factory=lambda: [
        "general knowledge",
        "planning",
        "writing improvement",
        "technical reasoning",
        "structure generation",
    ])
    forbidden_actions: list = field(default_factory=lambda: [
        "do not infer the user's real identity",
        "do not request raw private data",
        "do not reconstruct redacted fields",
        "do not output hidden identifiers",
        "do not assume private facts not provided",
        "do not claim access to local files",
    ])
    web_search_results: str = ""            # injected by pipeline after Tavily search
    output_format: str = "structured answer for the local guardian to merge"


# RedactionMap: placeholder token → original value
# Lives in memory for the duration of the pipeline; discarded after step 9 (D5).
RedactionMap = dict[str, str]


def sanitize(
    query: str,
    context: str,
    privacy_level: PrivacyLevel,
    model: GuardianModel,
) -> tuple[SanitizedPayload, RedactionMap]:
    """
    Sanitize query + context for online transmission.

    Returns (SanitizedPayload, redaction_map) where redaction_map maps
    placeholder → original_value (used by ResponseChecker in step 8).

    Raises BlockedError if:
      - privacy_level is SECRET (never online)
      - raw blacklisted patterns are detected in the context

    The final assert is a hard gate: if the sanitizer missed something,
    it surfaces here as an AssertionError rather than a silent data leak.
    """
    if privacy_level == PrivacyLevel.SECRET:
        raise BlockedError("SECRET-level content cannot be sanitized for online use.")

    if has_private_markers(context):
        raise BlockedError(
            "Context contains blacklisted patterns (passwords, keys, credentials). "
            "Cannot sanitize for online use."
        )

    sanitized_context = context
    redaction_map: RedactionMap = {}

    if privacy_level == PrivacyLevel.PRIVATE:
        sanitized_context = _guardian_anonymize(query, context, model)

    elif privacy_level == PrivacyLevel.LOW_SENSITIVE:
        sanitized_context, redaction_map = _redact_pii(context)

    elif privacy_level == PrivacyLevel.HIGHLY_PRIVATE:
        # Should not reach here (pipeline routes HIGHLY_PRIVATE to approval-required),
        # but guard defensively.
        sanitized_context = _guardian_anonymize(query, context, model)

    # Hard gate: assert the sanitizer did its job.
    # This fires as an AssertionError (caught by pipeline → local-only fallback).
    assert not has_private_markers(sanitized_context), (
        f"PayloadSanitizer: private markers remain after sanitization "
        f"(privacy_level={privacy_level}). This is a sanitizer bug."
    )

    payload = SanitizedPayload(
        route="guarded-online",
        privacy_level=privacy_level.value,
        user_question=query,
        sanitized_context=sanitized_context,
        local_context_summary="",
    )
    return payload, redaction_map


def build_hybrid_payload(
    query: str,
    local_context: str,
    privacy_level: PrivacyLevel,
    model: GuardianModel,
) -> tuple[SanitizedPayload, RedactionMap]:
    """
    Build a privacy-safe payload for the HYBRID_KNOWLEDGE_ONLY route.

    Calls Guardian to:
    1. Abstract the question (remove private references)
    2. Generate a one-line topic summary of local context (no raw data)

    Returns (SanitizedPayload, RedactionMap={}) — no raw private context is included.
    """
    prompt = (
        _HYBRID_PAYLOAD_PREFIX
        + f"Original question: {query}\n"
        + f"Local context (KEEP PRIVATE — summarize only): {local_context[:800]}\n\n"
        + "JSON:"
    )

    raw = model.generate(prompt, role="abstractor")

    abstracted_question = query
    local_context_summary = "local personal context available"

    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            abstracted_question = str(data.get("abstracted_question", query))
            local_context_summary = str(data.get("local_context_summary", local_context_summary))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    payload = SanitizedPayload(
        mode="hybrid_knowledge_only",
        route="hybrid-knowledge-only",
        privacy_level=privacy_level.value,
        user_question=abstracted_question,
        sanitized_context="",  # no raw context sent online
        local_context_summary=local_context_summary,
    )
    return payload, {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PII_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b'), 'EMAIL'),
    (re.compile(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b'), 'PHONE'),
    (re.compile(r'\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:St|Ave|Rd|Blvd|Dr|Ln|Way|Court|Ct)\b'), 'ADDRESS'),
]


def _redact_pii(text: str) -> tuple[str, RedactionMap]:
    """
    Regex-based PII redaction for LOW_SENSITIVE content.
    Returns (sanitized_text, redaction_map) where redaction_map is placeholder → original.
    """
    redaction_map: RedactionMap = {}
    counters: dict[str, int] = {}
    result = text

    for pattern, label in _PII_RULES:
        def _replace(m: re.Match, _label: str = label) -> str:
            original = m.group(0)
            counters[_label] = counters.get(_label, 0) + 1
            placeholder = f"[{_label}_{counters[_label]}]"
            redaction_map[placeholder] = original
            return placeholder

        result = pattern.sub(_replace, result)

    return result, redaction_map


def _guardian_anonymize(query: str, context: str, model: GuardianModel) -> str:
    """
    Ask the Guardian to produce an anonymized summary of the context.
    Used for PRIVATE and HIGHLY_PRIVATE content (after approval).
    """
    prompt = (
        _ANONYMIZE_PREFIX
        + f"Question: {query}\n\n"
        + f"Text to anonymize:\n{context[:1500]}\n\n"
        + "Anonymized text (no names, no contact info, no identifiers):"
    )
    return model.generate(prompt)
