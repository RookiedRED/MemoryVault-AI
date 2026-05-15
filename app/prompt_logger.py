"""
Prompt history logger.

Writes one JSONL entry per model call to logs/prompts.jsonl.
Captures local (Ollama/Guardian) and cloud (OpenAI) interactions separately.
The logs/ directory is gitignored — nothing here is ever committed.

Log entry shape:
{
  "ts":        "2026-05-14T12:34:56.789Z",   # UTC ISO-8601
  "side":      "local" | "cloud",
  "model":     "qwen2.5:7b" | "gpt-4o",
  "role":      "classifier" | "sanitizer" | "finalizer" | "expert",
  "query_id":  "uuid or null",
  "prompt":    "full prompt text",
  "response":  "full response text",
  "latency_ms": 312,
  "tokens":    {"prompt": 120, "completion": 88}   # cloud only; null for local
}
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "prompts.jsonl"


def _ensure_dir() -> None:
    _LOG_DIR.mkdir(exist_ok=True)


def log_local(
    *,
    model: str,
    role: str,
    prompt: str,
    response: str,
    latency_ms: int,
    query_id: Optional[str] = None,
) -> None:
    """Log a Guardian (Ollama) call."""
    _write(
        side="local",
        model=model,
        role=role,
        query_id=query_id,
        prompt=prompt,
        response=response,
        latency_ms=latency_ms,
        tokens=None,
    )


def log_cloud(
    *,
    model: str,
    role: str,
    prompt: str,
    response: str,
    latency_ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    query_id: Optional[str] = None,
) -> None:
    """Log an OpenAI (cloud) call."""
    _write(
        side="cloud",
        model=model,
        role=role,
        query_id=query_id,
        prompt=prompt,
        response=response,
        latency_ms=latency_ms,
        tokens={"prompt": prompt_tokens, "completion": completion_tokens},
    )


def _write(
    *,
    side: str,
    model: str,
    role: str,
    query_id: Optional[str],
    prompt: str,
    response: str,
    latency_ms: int,
    tokens,
) -> None:
    try:
        _ensure_dir()
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") +
                  f"{int(time.time() * 1000) % 1000:03d}Z",
            "side": side,
            "model": model,
            "role": role,
            "query_id": query_id,
            "prompt": prompt,
            "response": response,
            "latency_ms": latency_ms,
            "tokens": tokens,
        }
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never crash the main request path
        pass
