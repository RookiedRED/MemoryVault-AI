"""
Guardian Pipeline — 10-step orchestrator.

Step 1:  Understand the query.
Step 2:  Retrieve local context (sqlite-vec search — stub in Sprint 1).
Step 3:  Classify sensitivity (Guardian LLM → PrivacyLevel).
Step 4:  Decide route (local-only / guarded-online / approval-required / blocked).
Step 5:  Create sanitized payload (PayloadSanitizer + in-memory redaction map).
Step 6:  Preview payload if preview_sensitive_payloads=True.
Step 7:  Send sanitized payload to Expert Model.
Step 8:  ResponseChecker — scan for leakage; fallback to local on detection.
Step 9:  Merge Expert response with local context.
Step 10: Guardian finalizes the answer.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.config import DB_PATH, EXPERT_MAX_CONTEXT_CHARS, RETRIEVAL_DISTANCE_THRESHOLD, RETRIEVAL_TOP_K
from app.database import connection
from app.guardian.checker import check
from app.guardian.classifier import classify
from app.guardian.model import GuardianModel, GuardianUnavailableError
from app.guardian.sanitizer import BlockedError, SanitizedPayload, sanitize
from app.privacy.policy import policy_manager
from app.privacy.taxonomy import PrivacyLevel, RoutingDecision, default_route


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    answer: str
    routing: RoutingDecision
    privacy_level: PrivacyLevel
    sources: list[str]
    query_id: str
    status: str = "ok"          # ok | blocked
    warning: Optional[str] = None


@dataclass
class PendingApproval:
    query_id: str
    status: str = "pending_approval"
    preview: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    def __init__(
        self,
        guardian: GuardianModel,
        db_path: str = DB_PATH,
    ) -> None:
        self.guardian = guardian
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(
        self,
        query_text: str,
        force_route: Optional[RoutingDecision] = None,
    ) -> PipelineResult | PendingApproval:
        """
        Run the full 10-step pipeline.

        force_route overrides the default routing for LOCAL_ONLY / GUARDED_ONLINE.
        APPROVAL_REQUIRED and BLOCKED are always policy-enforced regardless of force_route.
        """
        query_id = str(uuid.uuid4())
        now = time.time()

        # Guardian availability check — D11: unavailable → local-only + warning
        if not self.guardian.is_available():
            self._log_query(query_id, query_text, RoutingDecision.LOCAL_ONLY, now)
            answer = self._finalize_local(query_text, "", query_id=query_id)
            return PipelineResult(
                answer=answer,
                routing=RoutingDecision.LOCAL_ONLY,
                privacy_level=PrivacyLevel.PRIVATE,
                sources=["local"],
                query_id=query_id,
                warning="Guardian offline — local-only mode",
            )

        # Step 1: query understood as-is (NLU expansion deferred to Sprint 3)

        # Step 2: retrieve local context (embedding search stub — Sprint 2)
        local_context = self._retrieve_local(query_text)

        # Step 3: classify
        try:
            privacy_level, confidence = classify(query_text, local_context, self.guardian, query_id=query_id)
        except GuardianUnavailableError:
            # Guardian went down between availability check and classify — safe fallback
            privacy_level, confidence = PrivacyLevel.PRIVATE, 0.0

        # Step 4: decide route
        routing = default_route(privacy_level)
        if force_route is not None:
            if force_route == RoutingDecision.LOCAL_ONLY:
                # /local-ask always wins — even HIGHLY_PRIVATE content can be answered locally
                routing = RoutingDecision.LOCAL_ONLY
            elif routing not in (RoutingDecision.BLOCKED, RoutingDecision.APPROVAL_REQUIRED):
                # Cannot force GUARDED_ONLINE past a policy block
                routing = force_route

        self._log_query(query_id, query_text, routing, now)
        self._log_classification(query_id, privacy_level, routing, confidence, now)

        if routing == RoutingDecision.BLOCKED:
            return PipelineResult(
                answer="This query has been blocked — it contains data classified as SECRET.",
                routing=routing,
                privacy_level=privacy_level,
                sources=[],
                query_id=query_id,
                status="blocked",
            )

        if routing == RoutingDecision.LOCAL_ONLY:
            return self._local_path(query_id, query_text, local_context, privacy_level, routing)

        if routing == RoutingDecision.APPROVAL_REQUIRED:
            return self._approval_path(query_id, query_text, local_context, privacy_level)

        # GUARDED_ONLINE
        return self._online_path(query_id, query_text, local_context, privacy_level)

    def resume(self, query_id: str, query_text: str) -> PipelineResult:
        """
        Resume a pending_approval query after the user confirms via POST /ask/:id/approve.
        Re-runs from step 5 (sanitize → Expert call → check → merge → finalize).
        """
        local_context = self._retrieve_local(query_text)
        return self._online_path(query_id, query_text, local_context, PrivacyLevel.HIGHLY_PRIVATE)

    # ------------------------------------------------------------------
    # Routing paths
    # ------------------------------------------------------------------

    def _local_path(
        self,
        query_id: str,
        query_text: str,
        local_context: str,
        privacy_level: PrivacyLevel,
        routing: RoutingDecision,
    ) -> PipelineResult:
        answer = self._finalize_local(query_text, local_context, query_id=query_id)
        return PipelineResult(
            answer=answer,
            routing=routing,
            privacy_level=privacy_level,
            sources=["local"],
            query_id=query_id,
        )

    def _approval_path(
        self,
        query_id: str,
        query_text: str,
        local_context: str,
        privacy_level: PrivacyLevel,
    ) -> PendingApproval:
        # Step 5 (preview only — Expert call happens only after POST /ask/:id/approve)
        try:
            payload, _ = sanitize(query_text, local_context, privacy_level, self.guardian)
            preview = {
                "sanitized_context": payload.sanitized_context,
                "privacy_level": payload.privacy_level,
                "forbidden_actions": payload.forbidden_actions,
            }
        except (BlockedError, AssertionError):
            preview = {"sanitized_context": "", "privacy_level": privacy_level.value}

        return PendingApproval(query_id=query_id, preview=preview)

    def _online_path(
        self,
        query_id: str,
        query_text: str,
        local_context: str,
        privacy_level: PrivacyLevel,
    ) -> PipelineResult:
        # Step 5: sanitize
        try:
            payload, redaction_map = sanitize(query_text, local_context, privacy_level, self.guardian)
        except (BlockedError, AssertionError):
            return self._local_path(query_id, query_text, local_context, privacy_level, RoutingDecision.LOCAL_ONLY)

        # Nothing useful to send — both question and context are empty
        if not payload.user_question.strip() and not payload.sanitized_context.strip():
            return self._local_path(query_id, query_text, local_context, privacy_level, RoutingDecision.LOCAL_ONLY)

        # Step 7: call Expert
        expert_response: Optional[str] = None
        status_code = 500
        prompt_tokens = 0
        completion_tokens = 0
        try:
            expert = self._get_expert()
            expert_response, prompt_tokens, completion_tokens = expert.call_with_usage(payload, query_id=query_id)
            status_code = 200
        except Exception:
            pass

        self._log_network_call(query_id, privacy_level, payload, status_code, prompt_tokens, completion_tokens)

        if expert_response is None:
            return self._local_path(query_id, query_text, local_context, privacy_level, RoutingDecision.LOCAL_ONLY)

        # Step 8: check response
        is_clean = check(expert_response, redaction_map)
        if not is_clean:
            self._update_leak(query_id)
            return self._local_path(query_id, query_text, local_context, privacy_level, RoutingDecision.LOCAL_ONLY)

        # Steps 9+10: merge and finalize
        answer = self._finalize_hybrid(query_text, local_context, expert_response, query_id=query_id)
        return PipelineResult(
            answer=answer,
            routing=RoutingDecision.GUARDED_ONLINE,
            privacy_level=privacy_level,
            sources=["local", "expert"],
            query_id=query_id,
        )

    # ------------------------------------------------------------------
    # Guardian helpers
    # ------------------------------------------------------------------

    def _retrieve_local(
        self,
        query_text: str,
        top_k: int = RETRIEVAL_TOP_K,
        distance_threshold: float = RETRIEVAL_DISTANCE_THRESHOLD,
    ) -> str:
        """
        Embed the query and retrieve the most relevant chunks from vec_chunks
        via cosine-distance search.

        Chunks whose cosine distance exceeds `distance_threshold` are dropped —
        they are semantically unrelated to the query and would only bloat the
        cloud prompt with irrelevant personal data.

        Returns empty string if the vault is empty, embedder is unavailable,
        or no chunks pass the relevance threshold.
        """
        try:
            from app.vault.embedder import embed_one, serialize
            vector = embed_one(query_text)
            blob = serialize(vector)
        except Exception:
            return ""

        try:
            from app.database import connection as db_connection
            with db_connection(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT c.text, v.distance "
                    "FROM vec_chunks v "
                    "JOIN chunks c ON c.rowid = v.rowid "
                    "WHERE v.embedding MATCH ? AND k = ?",
                    (blob, top_k),
                ).fetchall()
        except Exception:
            return ""

        # Drop chunks that are not meaningfully related to the query
        relevant = [row for row in rows if row["distance"] <= distance_threshold]
        if not relevant:
            return ""

        parts = [f"[chunk {i + 1}] {row['text']}" for i, row in enumerate(relevant)]
        context = "\n\n".join(parts)

        # Hard cap: never send more than EXPERT_MAX_CONTEXT_CHARS to the cloud
        if len(context) > EXPERT_MAX_CONTEXT_CHARS:
            context = context[:EXPERT_MAX_CONTEXT_CHARS] + "\n[context truncated]"

        return context

    def _finalize_local(self, query: str, context: str, query_id: str | None = None) -> str:
        if not self.guardian.is_available():
            return context or "No local context available."
        prompt = (
            f"Answer the following query using only the local context provided. "
            f"Be concise.\n\nQuery: {query}\n\nContext:\n{context or '(none)'}\n\nAnswer:"
        )
        try:
            return self.guardian.generate(prompt, role="finalizer", query_id=query_id)
        except GuardianUnavailableError:
            return context or "No local context available."

    def _finalize_hybrid(self, query: str, local_context: str, expert_response: str, query_id: str | None = None) -> str:
        prompt = (
            f"You have two sources of information to answer this query:\n\n"
            f"1. Local context:\n{local_context or '(none)'}\n\n"
            f"2. Expert response:\n{expert_response}\n\n"
            f"Query: {query}\n\n"
            f"Synthesize a concise, accurate answer. Prefer local context for personal details.\n\nAnswer:"
        )
        try:
            return self.guardian.generate(prompt, role="finalizer", query_id=query_id)
        except GuardianUnavailableError:
            return expert_response

    def _get_expert(self):
        from app.expert.openai_client import OpenAIExpertClient
        return OpenAIExpertClient()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _log_query(
        self,
        query_id: str,
        query_text: str,
        routing: RoutingDecision,
        now: float,
    ) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO queries (id, query_text, routing_decision, created_at) VALUES (?, ?, ?, ?)",
                (query_id, query_text, routing.value, now),
            )
            conn.commit()

    def _log_classification(
        self,
        query_id: str,
        privacy_level: PrivacyLevel,
        routing: RoutingDecision,
        confidence: float,
        now: float,
    ) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO query_classifications "
                "(id, query_id, privacy_level, classifier_used, confidence, routing_decision, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    query_id,
                    privacy_level.value,
                    "guardian_llm",
                    confidence,
                    routing.value,
                    now,
                ),
            )
            conn.commit()

    def _log_network_call(
        self,
        query_id: str,
        privacy_level: PrivacyLevel,
        payload: SanitizedPayload,
        status_code: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        import json

        preview = json.dumps({"sanitized_context": payload.sanitized_context[:200]})
        now = time.time()
        with connection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO network_audit_log "
                "(id, timestamp, mode, destination, query_id, payload_preview, "
                "response_status, user_consented, privacy_level, leak_detected, "
                "prompt_tokens, completion_tokens, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    now,
                    "guarded-online",
                    "openai_gpt4o",
                    query_id,
                    preview,
                    status_code,
                    1,
                    privacy_level.value,
                    0,
                    prompt_tokens,
                    completion_tokens,
                    now,
                ),
            )
            conn.commit()

    def _update_leak(self, query_id: str) -> None:
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE network_audit_log SET leak_detected = 1 WHERE query_id = ?",
                (query_id,),
            )
            conn.commit()
