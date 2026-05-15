"""
Privacy routes — policy management and dry-run routing.

GET   /privacy/policy          Read current privacy policy
PATCH /privacy/policy          Update one or more policy fields
POST  /privacy/policy/reset    Restore defaults
GET   /privacy/route           Dry-run: explain what route a query would take
POST  /privacy/scan            Scan arbitrary text for PII markers
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS
from app.guardian.classifier import classify
from app.guardian.model import GuardianModel
from app.privacy.markers import has_private_markers
from app.privacy.policy import policy_manager
from app.privacy.taxonomy import default_route

router = APIRouter(prefix="/privacy", tags=["privacy"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PolicyResponse(BaseModel):
    default_external_call: str
    raw_personal_data_online: bool
    identity_mapping_online: bool
    audit_all_online_calls: bool
    preview_sensitive_payloads: bool
    final_answer_checked_locally: bool


class PolicyUpdate(BaseModel):
    default_external_call: str | None = None
    raw_personal_data_online: bool | None = None
    identity_mapping_online: bool | None = None
    audit_all_online_calls: bool | None = None
    preview_sensitive_payloads: bool | None = None
    final_answer_checked_locally: bool | None = None


class RoutePreview(BaseModel):
    query: str
    privacy_level: str
    confidence: float
    routing: str
    reason: str


class ScanRequest(BaseModel):
    text: str


class ScanResponse(BaseModel):
    has_pii: bool
    detail: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy_to_response() -> PolicyResponse:
    p = policy_manager.get()
    return PolicyResponse(
        default_external_call=p.default_external_call,
        raw_personal_data_online=p.raw_personal_data_online,
        identity_mapping_online=p.identity_mapping_online,
        audit_all_online_calls=p.audit_all_online_calls,
        preview_sensitive_payloads=p.preview_sensitive_payloads,
        final_answer_checked_locally=p.final_answer_checked_locally,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/policy", response_model=PolicyResponse)
def get_policy() -> PolicyResponse:
    return _policy_to_response()


@router.patch("/policy", response_model=PolicyResponse)
def patch_policy(body: PolicyUpdate) -> PolicyResponse:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update.")
    try:
        policy_manager.update(**updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _policy_to_response()


@router.post("/policy/reset", response_model=PolicyResponse)
def reset_policy() -> PolicyResponse:
    policy_manager.reset()
    return _policy_to_response()


@router.get("/route", response_model=RoutePreview)
def preview_route(query: str) -> RoutePreview:
    """
    Dry-run: classify a query and return the routing decision without
    running the full pipeline or calling any external model.
    Guardian must be running locally for classification; if unavailable
    the response defaults to HIGHLY_PRIVATE / approval-required.
    """
    guardian = GuardianModel(
        base_url=OLLAMA_BASE_URL,
        model=OLLAMA_MODEL,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    privacy_level, confidence = classify(query, "", guardian)
    routing = default_route(privacy_level)
    return RoutePreview(
        query=query,
        privacy_level=privacy_level.value,
        confidence=round(confidence, 3),
        routing=routing.value,
        reason=f"Classified as {privacy_level.value} with confidence {confidence:.2f}.",
    )


@router.post("/scan", response_model=ScanResponse)
def scan(body: ScanRequest) -> ScanResponse:
    """Scan arbitrary text for PII markers using the local blacklist."""
    found = has_private_markers(body.text)
    return ScanResponse(
        has_pii=found,
        detail="PII or sensitive markers detected." if found else "No PII detected.",
    )
