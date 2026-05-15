"""
Dashboard routes — read-only backend telemetry for the operator UI.

GET /api/dashboard/stats    Aggregate usage stats from DB + prompt log
GET /api/dashboard/prompts  Paginated prompt log entries (from logs/prompts.jsonl)
GET /api/dashboard/models   Current model configuration and live availability
"""

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import (
    DB_PATH,
    EXPERT_MAX_CONTEXT_CHARS,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OPENAI_MODEL,
    RETRIEVAL_DISTANCE_THRESHOLD,
    RETRIEVAL_TOP_K,
)
from app.database import connection

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_LOG_FILE = Path(__file__).parent.parent.parent / "logs" / "prompts.jsonl"

# GPT-4o pricing (USD per 1M tokens) — update if OpenAI changes rates
_PRICE_INPUT_PER_1M = 5.00
_PRICE_OUTPUT_PER_1M = 15.00


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ModelInfo(BaseModel):
    name: str
    available: bool
    base_url: str | None = None


class ModelsResponse(BaseModel):
    guardian: ModelInfo
    expert: ModelInfo
    retrieval_top_k: int
    retrieval_distance_threshold: float
    expert_max_context_chars: int


class UsageStats(BaseModel):
    total_queries: int
    local_only: int
    guarded_online: int
    approval_required: int
    blocked: int
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float
    cloud_calls: int
    leak_detected: int
    avg_local_latency_ms: float | None
    avg_cloud_latency_ms: float | None


class PromptEntry(BaseModel):
    ts: str
    side: str
    model: str
    role: str
    query_id: str | None
    prompt: str
    response: str
    latency_ms: int
    tokens: dict | None


class PromptsResponse(BaseModel):
    entries: list[PromptEntry]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/models", response_model=ModelsResponse)
def models() -> ModelsResponse:
    """Return current model config and live availability."""
    from app.guardian.model import GuardianModel
    from app.expert.openai_client import OpenAIExpertClient

    g = GuardianModel()
    e = OpenAIExpertClient()

    return ModelsResponse(
        guardian=ModelInfo(
            name=OLLAMA_MODEL,
            available=g.is_available(),
            base_url=OLLAMA_BASE_URL,
        ),
        expert=ModelInfo(
            name=OPENAI_MODEL,
            available=e.is_available(),
        ),
        retrieval_top_k=RETRIEVAL_TOP_K,
        retrieval_distance_threshold=RETRIEVAL_DISTANCE_THRESHOLD,
        expert_max_context_chars=EXPERT_MAX_CONTEXT_CHARS,
    )


@router.get("/stats", response_model=UsageStats)
def stats() -> UsageStats:
    """Return aggregate usage stats from the database and prompt log."""
    with connection(DB_PATH) as conn:
        q = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(routing_decision = 'local_only')         AS local_only,
                SUM(routing_decision = 'guarded_online')     AS guarded_online,
                SUM(routing_decision = 'approval_required')  AS approval_required,
                SUM(routing_decision = 'blocked')            AS blocked
            FROM queries
            """
        ).fetchone()

        net = conn.execute(
            """
            SELECT
                COUNT(*)                    AS cloud_calls,
                SUM(prompt_tokens)          AS prompt_tokens,
                SUM(completion_tokens)      AS completion_tokens,
                SUM(leak_detected)          AS leaks
            FROM network_audit_log
            """
        ).fetchone()

    total_prompt = net["prompt_tokens"] or 0
    total_completion = net["completion_tokens"] or 0
    cost = (total_prompt / 1_000_000 * _PRICE_INPUT_PER_1M +
            total_completion / 1_000_000 * _PRICE_OUTPUT_PER_1M)

    # Latency averages come from the prompt log
    local_latencies, cloud_latencies = _read_latencies()
    avg_local = round(sum(local_latencies) / len(local_latencies), 1) if local_latencies else None
    avg_cloud = round(sum(cloud_latencies) / len(cloud_latencies), 1) if cloud_latencies else None

    return UsageStats(
        total_queries=q["total"] or 0,
        local_only=q["local_only"] or 0,
        guarded_online=q["guarded_online"] or 0,
        approval_required=q["approval_required"] or 0,
        blocked=q["blocked"] or 0,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        estimated_cost_usd=round(cost, 6),
        cloud_calls=net["cloud_calls"] or 0,
        leak_detected=net["leaks"] or 0,
        avg_local_latency_ms=avg_local,
        avg_cloud_latency_ms=avg_cloud,
    )


@router.get("/prompts", response_model=PromptsResponse)
def prompts(limit: int = 50, offset: int = 0, side: str = "all") -> PromptsResponse:
    """Return paginated prompt log entries, newest first."""
    all_entries = _read_log()

    if side != "all":
        all_entries = [e for e in all_entries if e["side"] == side]

    # Newest first
    all_entries.reverse()
    total = len(all_entries)
    page = all_entries[offset: offset + limit]

    return PromptsResponse(
        entries=[
            PromptEntry(
                ts=e["ts"],
                side=e["side"],
                model=e["model"],
                role=e["role"],
                query_id=e.get("query_id"),
                prompt=e["prompt"],
                response=e["response"],
                latency_ms=e["latency_ms"],
                tokens=e.get("tokens"),
            )
            for e in page
        ],
        total=total,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_log() -> list[dict]:
    if not _LOG_FILE.exists():
        return []
    entries = []
    with open(_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _read_latencies() -> tuple[list[float], list[float]]:
    local, cloud = [], []
    for e in _read_log():
        if e["side"] == "local":
            local.append(e["latency_ms"])
        else:
            cloud.append(e["latency_ms"])
    return local, cloud
