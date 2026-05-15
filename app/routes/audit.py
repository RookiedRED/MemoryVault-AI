"""
Audit routes — read-only views into the audit log.

GET /audit/log           List recent network_audit_log entries
GET /audit/log/{id}      Single audit entry by query_id
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DB_PATH
from app.database import connection

router = APIRouter(prefix="/audit", tags=["audit"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    id: str
    timestamp: float
    mode: str
    destination: str
    query_id: str
    payload_preview: str
    response_status: int
    user_consented: bool
    privacy_level: str
    leak_detected: bool


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/log", response_model=AuditLogResponse)
def audit_log(limit: int = 50, offset: int = 0) -> AuditLogResponse:
    """Return paginated network audit log entries, newest first."""
    with connection(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM network_audit_log").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM network_audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    entries = [
        AuditEntry(
            id=row["id"],
            timestamp=row["timestamp"],
            mode=row["mode"],
            destination=row["destination"],
            query_id=row["query_id"],
            payload_preview=row["payload_preview"],
            response_status=row["response_status"],
            user_consented=bool(row["user_consented"]),
            privacy_level=row["privacy_level"],
            leak_detected=bool(row["leak_detected"]),
        )
        for row in rows
    ]
    return AuditLogResponse(entries=entries, total=total)


@router.get("/log/{query_id}", response_model=AuditEntry)
def audit_entry(query_id: str) -> AuditEntry:
    """Return the audit log entry for a specific query_id."""
    with connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM network_audit_log WHERE query_id = ?", (query_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No audit entry for query_id={query_id!r}.")

    return AuditEntry(
        id=row["id"],
        timestamp=row["timestamp"],
        mode=row["mode"],
        destination=row["destination"],
        query_id=row["query_id"],
        payload_preview=row["payload_preview"],
        response_status=row["response_status"],
        user_consented=bool(row["user_consented"]),
        privacy_level=row["privacy_level"],
        leak_detected=bool(row["leak_detected"]),
    )
