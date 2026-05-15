"""
Mobile upload pairing routes.

GET  /api/status   — server capability status
GET  /api/pair     — generate QR code and pairing token
POST /api/upload   — upload file via mobile using token
"""

import os
import secrets
import socket
import string
import time
from io import BytesIO

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import DB_PATH, OPENAI_API_KEY, PORT
from app.vault.importer import DuplicateFileError, import_file

router = APIRouter(prefix="/api", tags=["mobile"])

# In-memory token store: {token: expires_at}
_tokens: dict[str, float] = {}
_TOKEN_TTL = 600  # 10 minutes


def _purge_expired() -> None:
    now = time.time()
    expired = [t for t, exp in _tokens.items() if exp <= now]
    for t in expired:
        del _tokens[t]


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _generate_token(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_qr_svg(url: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg

        factory = qrcode.image.svg.SvgPathImage
        qr = qrcode.make(url, image_factory=factory, box_size=10, border=2)
        buf = BytesIO()
        qr.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        # Fallback: return a simple placeholder SVG
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
            '<rect width="200" height="200" fill="#f2f2f7"/>'
            '<text x="100" y="105" text-anchor="middle" font-size="12" fill="#8e8e93">QR unavailable</text>'
            "</svg>"
        )


@router.get("/status")
def api_status() -> dict:
    """Return local server capability flags."""
    from app.guardian.model import GuardianModel

    guardian = GuardianModel().is_available()
    online = bool(OPENAI_API_KEY)
    return {"local": True, "guardian": guardian, "online": online}


@router.get("/pair")
def api_pair() -> dict:
    """Generate a pairing token and QR code for mobile upload."""
    _purge_expired()
    token = _generate_token()
    expires_at = time.time() + _TOKEN_TTL
    _tokens[token] = expires_at

    ip = _get_local_ip()
    url = f"http://{ip}:{PORT}/mobile?token={token}"
    qr_svg = _generate_qr_svg(url)

    return {
        "token": token,
        "url": url,
        "qr_svg": qr_svg,
        "expires_in": _TOKEN_TTL,
    }


@router.post("/upload")
async def api_upload(file: UploadFile, token: str) -> dict:
    """Upload a file via mobile using a pairing token."""
    _purge_expired()

    if token not in _tokens:
        raise HTTPException(status_code=403, detail="Invalid or expired token.")

    if _tokens[token] <= time.time():
        del _tokens[token]
        raise HTTPException(status_code=403, detail="Token expired.")

    # 50 MB limit
    MAX_SIZE = 50 * 1024 * 1024
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB).")

    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"

    try:
        memory_id = import_file(data, filename, mime, DB_PATH)
    except DuplicateFileError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"File already imported (memory_id={exc}).",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"memory_id": memory_id, "filename": filename, "status": "imported"}
