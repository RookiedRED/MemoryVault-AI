import json
import re
from dataclasses import dataclass

import app.guardian.cache as cache
from app.guardian.model import GuardianModel
from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision

_ANALYZE_PROMPT = """\
You are a privacy-aware routing analyst for a personal AI memory vault.
Analyze the user query and the retrieved local context, then decide the routing.

Return ONLY valid JSON — no explanation, no markdown:
{{
  "privacy_level": "PUBLIC | LOW_SENSITIVE | PRIVATE | HIGHLY_PRIVATE | SECRET",
  "local_sufficiency": "LOCAL_SUFFICIENT | LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL | LOCAL_MISSING_EXTERNAL_ONLY | LOCAL_PRIVATE_BLOCKED",
  "recommended_route": "local-only | guarded-online | hybrid-knowledge-only | approval-required | blocked",
  "needs_local_retrieval": true/false,
  "needs_online_model": true/false,
  "redaction_required": true/false,
  "reason": "one sentence",
  "confidence": 0.0-1.0
}}

Routing rules:
- LOCAL_SUFFICIENT or LOCAL_PRIVATE_BLOCKED → local-only
- SECRET → blocked
- HIGHLY_PRIVATE + needs online → approval-required
- PRIVATE + needs online → hybrid-knowledge-only (do NOT send private context online)
- LOW_SENSITIVE or PUBLIC + needs online → guarded-online
- LOCAL_MISSING_EXTERNAL_ONLY (no private data needed) → guarded-online

Privacy levels:
  PUBLIC        — general knowledge, no personal info
  LOW_SENSITIVE — personal but not sensitive (job title, location, preferences)
  PRIVATE       — should stay private (career notes, plans, opinions)
  HIGHLY_PRIVATE — sensitive (medical, legal, financial, relationship)
  SECRET        — credentials, passwords, API keys, auth tokens

Local sufficiency:
  LOCAL_SUFFICIENT               — local data alone fully answers the question
  LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL — local data exists but external knowledge would improve the answer
  LOCAL_MISSING_EXTERNAL_ONLY    — no private data needed; online model handles it
  LOCAL_PRIVATE_BLOCKED          — data too sensitive to share; answer locally or explain limitation

Query: {query}
Retrieved local context: {context_snippet}

JSON:"""


@dataclass
class AnalysisResult:
    privacy_level: PrivacyLevel
    local_sufficiency: LocalSufficiency
    recommended_route: RoutingDecision
    needs_local_retrieval: bool
    needs_online_model: bool
    redaction_required: bool
    reason: str
    confidence: float


def _fallback_result() -> AnalysisResult:
    return AnalysisResult(
        privacy_level=PrivacyLevel.HIGHLY_PRIVATE,
        local_sufficiency=LocalSufficiency.LOCAL_PRIVATE_BLOCKED,
        recommended_route=RoutingDecision.LOCAL_ONLY,
        needs_local_retrieval=False,
        needs_online_model=False,
        redaction_required=False,
        reason="Parse failure — defaulting to safe local-only.",
        confidence=0.5,
    )


def analyze(
    query: str,
    context: str,
    model: GuardianModel,
    query_id: str | None = None,
) -> AnalysisResult:
    """
    Full privacy analysis of a query + context.

    Returns AnalysisResult with all routing fields.
    Falls back to HIGHLY_PRIVATE / LOCAL_PRIVATE_BLOCKED / LOCAL_ONLY on parse failure.
    """
    context_snippet = context[:400] if context else "(none)"

    # Cache hit — skip Ollama call
    cached = cache.get(query, context_snippet)
    if cached is not None:
        return cached

    prompt = _ANALYZE_PROMPT.format(query=query, context_snippet=context_snippet)
    raw = model.generate(prompt, role="analyzer", query_id=query_id)

    # Extract the first JSON object from the response
    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if not match:
        return _fallback_result()

    try:
        data = json.loads(match.group(0))
        result = AnalysisResult(
            privacy_level=PrivacyLevel(data["privacy_level"]),
            local_sufficiency=LocalSufficiency(data["local_sufficiency"]),
            recommended_route=RoutingDecision(data["recommended_route"]),
            needs_local_retrieval=bool(data.get("needs_local_retrieval", False)),
            needs_online_model=bool(data.get("needs_online_model", False)),
            redaction_required=bool(data.get("redaction_required", False)),
            reason=str(data.get("reason", "")),
            confidence=float(data.get("confidence", 0.7)),
        )
        cache.put(query, context_snippet, result)
        return result
    except (json.JSONDecodeError, KeyError, ValueError):
        return _fallback_result()
