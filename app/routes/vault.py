"""
Vault routes — import files, check status, list and delete memories.

POST   /vault/import              Ingest a file into the local vault (local-only)
GET    /vault/status              How many memories and chunks are stored
GET    /vault/memories            List all memories (paginated)
GET    /vault/memories/{id}       Get a single memory with its chunks
DELETE /vault/memories/{id}       Delete a memory and all its chunks
"""

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import DB_PATH
from app.database import connection
from app.vault.importer import DuplicateFileError, import_file

router = APIRouter(prefix="/vault", tags=["vault"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ImportResponse(BaseModel):
    memory_id: str
    filename: str
    file_size: int
    status: str


class VaultStatus(BaseModel):
    memory_count: int
    chunk_count: int


class MemorySummary(BaseModel):
    id: str
    filename: str
    mime_type: str
    file_size: int
    status: str
    chunk_count: int
    created_at: float


class ChunkSummary(BaseModel):
    id: str
    chunk_index: int
    text: str
    token_count: int


class MemoryDetail(BaseModel):
    id: str
    filename: str
    mime_type: str
    file_size: int
    status: str
    created_at: float
    chunks: list[ChunkSummary]


class MemoryListResponse(BaseModel):
    memories: list[MemorySummary]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ImportResponse)
async def vault_import(file: UploadFile) -> ImportResponse:
    """
    Ingest a file into the vault.
    Text is extracted, chunked, embedded, and stored — all locally.
    No network calls are made.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "unknown"

    try:
        memory_id = import_file(data, filename, mime, DB_PATH)
    except DuplicateFileError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"File already imported (memory_id={exc}).",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ImportResponse(
        memory_id=memory_id,
        filename=filename,
        file_size=len(data),
        status="imported",
    )


@router.get("/status", response_model=VaultStatus)
def vault_status() -> VaultStatus:
    """Return the count of memories and chunks stored in the local vault."""
    with connection(DB_PATH) as conn:
        memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return VaultStatus(memory_count=memory_count, chunk_count=chunk_count)


@router.get("/memories", response_model=MemoryListResponse)
def list_memories(limit: int = 50, offset: int = 0) -> MemoryListResponse:
    """List all memories in the vault, newest first."""
    with connection(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        rows = conn.execute(
            "SELECT m.*, COUNT(c.id) AS chunk_count "
            "FROM memories m LEFT JOIN chunks c ON c.memory_id = m.id "
            "GROUP BY m.id ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    memories = [
        MemorySummary(
            id=row["id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            file_size=row["file_size"],
            status=row["status"],
            chunk_count=row["chunk_count"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return MemoryListResponse(memories=memories, total=total)


@router.get("/memories/{memory_id}", response_model=MemoryDetail)
def get_memory(memory_id: str) -> MemoryDetail:
    """Return a memory record and all its text chunks."""
    with connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Memory {memory_id!r} not found.")

        chunk_rows = conn.execute(
            "SELECT * FROM chunks WHERE memory_id = ? ORDER BY chunk_index",
            (memory_id,),
        ).fetchall()

    return MemoryDetail(
        id=row["id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        file_size=row["file_size"],
        status=row["status"],
        created_at=row["created_at"],
        chunks=[
            ChunkSummary(
                id=c["id"],
                chunk_index=c["chunk_index"],
                text=c["text"],
                token_count=c["token_count"],
            )
            for c in chunk_rows
        ],
    )


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str) -> None:
    """
    Delete a memory and all its chunks (cascades to vec_chunks via FK).
    Returns 204 No Content on success.
    """
    with connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Memory {memory_id!r} not found.")
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
