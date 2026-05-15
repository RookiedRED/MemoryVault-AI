import time

import httpx

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS


class GuardianUnavailableError(Exception):
    """Raised when the Guardian Model (Ollama) cannot be reached."""


class GuardianModel:
    """
    HTTP client for the local Guardian Model via Ollama.

    The Guardian runs 3 times per guarded-online query:
      - Step 3: classify sensitivity
      - Step 5: anonymize/summarize context (PRIVATE level)
      - Step 10: finalize the answer

    On unavailability, callers should catch GuardianUnavailableError and
    fall back to local-only mode (pipeline.py handles this).
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout

    def is_available(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, *, role: str = "guardian", query_id: str | None = None) -> str:
        """Send a prompt and return the generated text. Raises GuardianUnavailableError on failure."""
        t0 = time.monotonic()
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            response_text = resp.json()["response"]
        except httpx.TimeoutException as exc:
            raise GuardianUnavailableError(f"Guardian timed out after {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise GuardianUnavailableError(f"Guardian HTTP error: {exc}") from exc
        except Exception as exc:
            raise GuardianUnavailableError(f"Guardian unavailable: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            from app.prompt_logger import log_local
            log_local(
                model=self.model,
                role=role,
                prompt=prompt,
                response=response_text,
                latency_ms=latency_ms,
                query_id=query_id,
            )
        except Exception:
            pass

        return response_text
