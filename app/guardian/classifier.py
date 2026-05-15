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

=== SUFFICIENCY DEFINITIONS (read carefully) ===

LOCAL_SUFFICIENT:
  The local vault content DIRECTLY and COMPLETELY answers this specific question.
  The user would be fully satisfied with only local data.
  Example: "What is my phone number?" when the vault contains a resume with the number.

LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL:
  Local data is relevant background, BUT the question also requires external knowledge,
  current market data, real-time information, internet search, or broader expertise.
  The online model adds significant value that local data cannot provide.
  Example: "Based on my resume, which companies should I apply to?" — resume is
  background context, but current company listings and market info come from the internet.
  Example: "Write a cover letter using my resume" — resume needed locally, writing
  help and job market knowledge from online.

LOCAL_MISSING_EXTERNAL_ONLY:
  No private personal data is needed. The question is fully answerable with public
  or general knowledge alone.
  Example: "What is Python?", "Explain machine learning", "Today's weather".

LOCAL_PRIVATE_BLOCKED:
  The needed data is too sensitive to share online and the local model must answer
  alone or explain the limitation.

=== CRITICAL RULE: needs_online_model ===
Set needs_online_model = true whenever the query asks about ANY of:
  - current market conditions, industry trends, job listings, company names
  - real-time information (news, stock prices, weather, events)
  - general knowledge not stored in the vault
  - writing improvement, drafting, or structured generation
  - technical advice beyond the local context

Having relevant LOCAL context does NOT mean needs_online_model = false.
A resume tells you ABOUT the person but cannot tell you WHICH COMPANIES ARE HIRING.

=== ROUTING RULES ===
- LOCAL_SUFFICIENT or LOCAL_PRIVATE_BLOCKED → local-only
- SECRET → blocked
- HIGHLY_PRIVATE + needs online → approval-required
- PRIVATE + needs online → hybrid-knowledge-only (do NOT send private context online)
- LOW_SENSITIVE or PUBLIC + needs online → guarded-online
- LOCAL_MISSING_EXTERNAL_ONLY → guarded-online

=== PRIVACY LEVELS ===
  PUBLIC        — general knowledge, no personal info
  LOW_SENSITIVE — personal but not sensitive (resume, job title, skills, location)
  PRIVATE       — should stay private (personal notes, opinions, plans)
  HIGHLY_PRIVATE — sensitive (medical, legal, financial, relationship details)
  SECRET        — credentials, passwords, API keys, auth tokens

=== EXAMPLES ===
Query: "那目前市場上有什麼公司我可以投履歷嗎"
Context: resume with work experience
→ local_sufficiency: LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL
→ needs_online_model: true  (market/company data not in vault)
→ privacy_level: LOW_SENSITIVE  (resume background)
→ recommended_route: guarded-online

Query: "幫我根據履歷寫求職信"
Context: resume
→ local_sufficiency: LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL
→ needs_online_model: true  (writing expertise from online)
→ privacy_level: LOW_SENSITIVE
→ recommended_route: guarded-online

Query: "今天天氣怎樣"
Context: resume (irrelevant)
→ local_sufficiency: LOCAL_MISSING_EXTERNAL_ONLY
→ needs_online_model: true
→ privacy_level: PUBLIC
→ recommended_route: guarded-online

Query: "我的電話號碼是幾號"
Context: resume with phone number
→ local_sufficiency: LOCAL_SUFFICIENT
→ needs_online_model: false
→ recommended_route: local-only

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
    context_snippet = context[:500] if context else "(none)"

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
