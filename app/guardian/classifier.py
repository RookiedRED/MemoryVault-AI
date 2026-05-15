import json
import re

import app.guardian.cache as cache
from app.guardian.model import GuardianModel
from app.privacy.taxonomy import PrivacyLevel

_CLASSIFY_PROMPT = """\
You are a privacy classifier. Classify the sensitivity of the user query below.

Return ONLY a valid JSON object — no explanation, no markdown, no extra text:
{{"privacy_level": "<LEVEL>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}

Privacy levels (choose exactly one):
  PUBLIC        — general knowledge, no personal info
  LOW_SENSITIVE — personal but not sensitive (job title, general location, preferences)
  PRIVATE       — personal info that should stay private (career notes, opinions, plans)
  HIGHLY_PRIVATE — sensitive personal info (medical, legal, financial, relationship details)
  SECRET        — credentials, passwords, API keys, government IDs, auth tokens

Query: {query}
Relevant context snippet: {context_snippet}

JSON:"""


def classify(
    query: str,
    context: str,
    model: GuardianModel,
    query_id: str | None = None,
) -> tuple[PrivacyLevel, float]:
    """
    Classify the sensitivity of a query + context.

    Returns (PrivacyLevel, confidence: 0.0-1.0).
    Falls back to HIGHLY_PRIVATE on parse failure — safer to over-restrict than under-restrict.
    """
    context_snippet = context[:400] if context else "(none)"

    # Cache hit — skip Ollama call
    cached = cache.get(query, context_snippet)
    if cached is not None:
        return cached

    prompt = _CLASSIFY_PROMPT.format(query=query, context_snippet=context_snippet)
    raw = model.generate(prompt, role="classifier", query_id=query_id)

    # Extract the first JSON object from the response
    match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
    if not match:
        return PrivacyLevel.HIGHLY_PRIVATE, 0.5

    try:
        data = json.loads(match.group(0))
        level = PrivacyLevel(data["privacy_level"])
        confidence = float(data.get("confidence", 0.7))
        cache.put(query, context_snippet, level, confidence)
        return level, confidence
    except (json.JSONDecodeError, KeyError, ValueError):
        return PrivacyLevel.HIGHLY_PRIVATE, 0.5
