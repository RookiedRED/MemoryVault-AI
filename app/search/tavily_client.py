"""
Tavily web search client.

Performs a live web search and returns a formatted snippet string suitable
for injection into the expert model prompt as grounded, up-to-date context.

Only called when Guardian determines needs_online_model=True and the query
requires external knowledge (LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL or
LOCAL_MISSING_EXTERNAL_ONLY). Failures are silent — the pipeline continues
without search results rather than blocking the response.
"""

from __future__ import annotations

import time
from typing import Optional

from app.config import TAVILY_API_KEY, TAVILY_MAX_RESULTS


def search(query: str, max_results: int = TAVILY_MAX_RESULTS, query_id: Optional[str] = None) -> Optional[str]:
    """
    Run a web search via Tavily and return a formatted string of results.

    Returns None if:
    - TAVILY_API_KEY is not set
    - tavily package is not installed
    - the search call fails for any reason

    The returned string is ready to embed in a prompt as-is.
    """
    if not TAVILY_API_KEY:
        return None

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)

        t0 = time.monotonic()
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",  # "basic" = faster + cheaper; "advanced" = deeper
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        results = response.get("results", [])
        if not results:
            return None

        parts: list[str] = []
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "").strip()
            url = r.get("url", "").strip()
            content = r.get("content", "").strip()
            # Keep each snippet concise so we don't flood the context window
            snippet = content[:500] if len(content) > 500 else content
            parts.append(f"[{i}] {title}\nSource: {url}\n{snippet}")

        formatted = "\n\n".join(parts)

        try:
            from app.prompt_logger import log_search
            log_search(
                query=query,
                results=formatted,
                latency_ms=latency_ms,
                num_results=len(parts),
                query_id=query_id,
            )
        except Exception:
            pass

        return formatted

    except ImportError:
        return None
    except Exception:
        return None


def is_available() -> bool:
    """Return True if Tavily is configured and the package is installed."""
    if not TAVILY_API_KEY:
        return False
    try:
        import tavily  # noqa: F401
        return True
    except ImportError:
        return False
