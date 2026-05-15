import time
from typing import Optional

from app.config import OPENAI_API_KEY, OPENAI_MAX_TOKENS, OPENAI_MODEL
from app.expert.base import ExpertModelClient
from app.expert.rate_limiter import RateLimitExceededError, rate_limiter

# Static system message — extracted as a module-level constant so OpenAI's
# automatic prompt caching can match the identical prefix across every call
# and apply the 50% cached-token discount (requires ≥1024 tokens; this alone
# is short, but paired with a long sanitized_context prefix it crosses the
# threshold). Must never be built dynamically inside call_with_usage().
_SYSTEM_MSG = (
    "You are a helpful assistant. You MUST follow all of these rules:\n"
    "- do not infer the user's real identity\n"
    "- do not request raw private data\n"
    "- do not reconstruct redacted fields\n"
    "- do not output hidden identifiers\n"
    "- do not assume private facts not provided\n"
    "- do not claim access to local files"
)


class OpenAIExpertClient(ExpertModelClient):
    """
    Expert Model client backed by OpenAI GPT-4o.

    The forbidden_actions from the payload are embedded in the system prompt.
    Note: these are instructions to the model, not hard constraints — ResponseChecker
    (step 8) is the actual enforcement gate.

    Returns (answer_text, prompt_tokens, completion_tokens) via call_with_usage().
    The plain call() method is kept for backward compatibility.
    """

    def call(self, payload) -> str:
        text, _, _ = self.call_with_usage(payload)
        return text

    def call_with_usage(self, payload, query_id: Optional[str] = None) -> tuple[str, int, int]:
        """
        Call the Expert Model and return (answer, prompt_tokens, completion_tokens).
        Raises RateLimitExceededError if the sliding-window limit is hit.
        """
        import openai

        rate_limiter.check_and_record()

        client = openai.OpenAI(api_key=OPENAI_API_KEY)

        system_msg = _SYSTEM_MSG
        web_section = (
            f"\n\n=== Real-time Web Search Results ===\n{payload.web_search_results}"
            if getattr(payload, "web_search_results", "")
            else ""
        )
        user_msg = (
            f"Task: {payload.task or 'Answer the question using the provided context.'}\n"
            f"Privacy level: {payload.privacy_level}\n"
            f"Question: {payload.user_question}\n\n"
            f"Context:\n{payload.sanitized_context}"
            f"{web_section}"
        )
        full_prompt = f"[SYSTEM]\n{system_msg}\n\n[USER]\n{user_msg}"

        t0 = time.monotonic()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=OPENAI_MAX_TOKENS,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        answer = response.choices[0].message.content

        try:
            from app.prompt_logger import log_cloud
            log_cloud(
                model=OPENAI_MODEL,
                role="expert",
                prompt=full_prompt,
                response=answer,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                query_id=query_id,
            )
        except Exception:
            pass

        return answer, prompt_tokens, completion_tokens

    def is_available(self) -> bool:
        return bool(OPENAI_API_KEY)
