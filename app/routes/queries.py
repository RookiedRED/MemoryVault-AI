"""
Query history routes.

GET /queries          List past queries with routing decisions (paginated)
GET /queries/{id}     Get a single query record
GET /queries/stats    Routing breakdown counts
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH
from app.database import connection

router = APIRouter(prefix="/queries", tags=["queries"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QueryRecord(BaseModel):
    id: str
    query_text: str
    routing_decision: str
    created_at: float


class QueryListResponse(BaseModel):
    queries: list[QueryRecord]
    total: int


class RoutingStats(BaseModel):
    local_only: int
    guarded_online: int
    approval_required: int
    blocked: int
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=QueryListResponse)
def list_queries(limit: int = 50, offset: int = 0) -> QueryListResponse:
    """Return past queries newest-first, paginated."""
    with connection(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM queries ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    return QueryListResponse(
        queries=[
            QueryRecord(
                id=row["id"],
                query_text=row["query_text"],
                routing_decision=row["routing_decision"],
                created_at=row["created_at"],
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/stats", response_model=RoutingStats)
def routing_stats() -> RoutingStats:
    """Return a count breakdown of routing decisions across all queries."""
    with connection(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT routing_decision, COUNT(*) AS cnt FROM queries GROUP BY routing_decision"
        ).fetchall()

    counts = {row["routing_decision"]: row["cnt"] for row in rows}
    total = sum(counts.values())
    return RoutingStats(
        local_only=counts.get("local-only", 0),
        guarded_online=counts.get("guarded-online", 0),
        approval_required=counts.get("approval-required", 0),
        blocked=counts.get("blocked", 0),
        total=total,
    )


@router.get("/{query_id}", response_model=QueryRecord)
def get_query(query_id: str) -> QueryRecord:
    """Return a single query record by ID."""
    with connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM queries WHERE id = ?", (query_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Query {query_id!r} not found.")

    return QueryRecord(
        id=row["id"],
        query_text=row["query_text"],
        routing_decision=row["routing_decision"],
        created_at=row["created_at"],
    )
