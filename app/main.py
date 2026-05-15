from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import DB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS
from app.database import init_db
from app.routes.ask import router as ask_router
from app.routes.audit import router as audit_router
from app.routes.mobile import router as mobile_router
from app.routes.privacy import router as privacy_router
from app.routes.queries import router as queries_router
from app.routes.vault import router as vault_router

_STATIC = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(DB_PATH)
    _warmup_guardian()
    yield


def _warmup_guardian() -> None:
    """
    Touch Ollama on startup so the model is loaded into memory before the
    first real request arrives. Failures are silently ignored — the pipeline
    handles Guardian unavailability at request time.
    """
    try:
        from app.guardian.model import GuardianModel
        g = GuardianModel(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        if g.is_available():
            # A minimal generate call forces the model into GPU/RAM
            g.generate("ping", log=False)
    except Exception:
        pass


app = FastAPI(
    title="MemoryVault AI",
    description="Privacy-preserving personal AI memory vault",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ask_router)
app.include_router(vault_router)
app.include_router(privacy_router)
app.include_router(audit_router)
app.include_router(queries_router)
app.include_router(mobile_router)

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/", tags=["ui"])
def ui() -> FileResponse:
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/mobile", tags=["ui"])
def mobile_ui() -> FileResponse:
    return FileResponse(str(_STATIC / "mobile.html"))


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}
