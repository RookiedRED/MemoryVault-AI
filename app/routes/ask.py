"""
Ask routes — the main query interface.

POST /ask              Run the full pipeline (auto-route)
POST /ask/local        Force local-only path
POST /ask/online       Force guarded-online path
POST /ask/{id}/approve Resume a pending_approval query
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS
from app.guardian.model import GuardianModel
from app.guardian.pipeline import PendingApproval, Pipeline, PipelineResult
from app.privacy.taxonomy import RoutingDecision

router = APIRouter(prefix="/ask", tags=["ask"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    query: str


class AskResponse(BaseModel):
    query_id: str
    status: str          # "ok" | "blocked" | "pending_approval"
    answer: str | None = None
    routing: str | None = None
    privacy_level: str | None = None
    sources: list[str] = []
    warning: str | None = None
    preview: dict | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline() -> Pipeline:
    guardian = GuardianModel(
        base_url=OLLAMA_BASE_URL,
        model=OLLAMA_MODEL,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    return Pipeline(guardian=guardian, db_path=DB_PATH)


def _to_response(result: PipelineResult | PendingApproval) -> AskResponse:
    if isinstance(result, PendingApproval):
        return AskResponse(
            query_id=result.query_id,
            status=result.status,
            preview=result.preview,
        )
    return AskResponse(
        query_id=result.query_id,
        status=result.status,
        answer=result.answer,
        routing=result.routing.value,
        privacy_level=result.privacy_level.value,
        sources=result.sources,
        warning=result.warning,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    """Run the full Guardian pipeline with automatic routing."""
    pipeline = _make_pipeline()
    result = pipeline.run(body.query)
    return _to_response(result)


@router.post("/local", response_model=AskResponse)
def ask_local(body: AskRequest) -> AskResponse:
    """Force local-only path — never contacts an external model."""
    pipeline = _make_pipeline()
    result = pipeline.run(body.query, force_route=RoutingDecision.LOCAL_ONLY)
    return _to_response(result)


@router.post("/online", response_model=AskResponse)
def ask_online(body: AskRequest) -> AskResponse:
    """Force guarded-online path — sanitize then send to Expert Model."""
    pipeline = _make_pipeline()
    result = pipeline.run(body.query, force_route=RoutingDecision.GUARDED_ONLINE)
    return _to_response(result)


@router.post("/{query_id}/approve", response_model=AskResponse)
def approve(query_id: str, body: AskRequest) -> AskResponse:
    """
    Resume a pending_approval query after the user confirms.
    The original query text must be re-sent in the request body.
    """
    pipeline = _make_pipeline()
    try:
        result = pipeline.resume(query_id, body.query)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(result)
