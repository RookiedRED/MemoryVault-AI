"""
Guardian Pipeline — 10-step orchestrator.

Step 1:  Understand the query.
Step 2:  Retrieve local context (sqlite-vec search — stub in Sprint 1).
Step 3:  Analyze sensitivity (Guardian LLM → AnalysisResult).
Step 4:  Decide route (local-only / guarded-online / hybrid-knowledge-only / approval-required / blocked).
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
from app.guardian.classifier import AnalysisResult, analyze
from app.guardian.model import GuardianModel, GuardianUnavailableError
from app.guardian.sanitizer import BlockedError, SanitizedPayload, build_hybrid_payload, sanitize
from app.privacy.policy import policy_manager
from app.privacy.taxonomy import LocalSufficiency, PrivacyLevel, RoutingDecision, default_route

# ---------------------------------------------------------------------------
# Module-level prompt prefixes (Ollama KV-cache optimisation)
#
# Ollama caches the KV state for any prompt whose leading tokens are identical
# to a previous call. By keeping these static prefixes as module-level
# constants (not rebuilt inside methods), we guarantee the same byte sequence
# appears at the start of every prompt, maximising KV-cache hits and cutting
# Guardian latency significantly after the first call in a session.
# ---------------------------------------------------------------------------

_LOCAL_FINALIZE_PREFIX = (
    "You are a private personal assistant with access only to the user's local vault.\n"
    "Answer using ONLY the local context provided. If insufficient, say so clearly.\n"
    "Do not guess or hallucinate facts not in the context.\n\n"
)

_HYBRID_FINALIZE_PREFIX = (
    "You are a personal assistant combining local private knowledge with general online advice.\n\n"
    "Your task: produce a final, coherent answer for the user.\n\n"
    "Guidelines:\n"
    "- Use local facts for personal specifics. Do not expose raw personal data.\n"
    "- Use the online reasoning for general knowledge, structure, and suggestions.\n"
    "- Merge them into one natural response.\n"
    "- Add a note if any assumption was made.\n"
    "- Do not reveal that two separate models were used.\n\n"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    answer: str
    routing: RoutingDecision
    privacy_level: PrivacyLevel
    local_sufficiency: LocalSufficiency
    sources: list[str]
    query_id: str
    status: str = "ok"          # ok | blocked
    warning: Optional[str] = None
    routing_detail: dict = field(default_factory=dict)


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
    ) -> "PipelineResult | PendingApproval":
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
                local_sufficiency=LocalSufficiency.LOCAL_PRIVATE_BLOCKED,
                sources=["local"],
                query_id=query_id,
                warning="Guardian offline — local-only mode",
            )

        # Step 1: query understood as-is (NLU expansion deferred to Sprint 3)

        # Step 2: retrieve local context (embedding search stub — Sprint 2)
        local_context = self._retrieve_local(query_text)

        # Step 3: analyze
        try:
            analysis = analyze(query_text, local_context, self.guardian, query_id=query_id)
        except GuardianUnavailableError:
            # Guardian went down between availability check and analyze — safe fallback
            analysis = AnalysisResult(
                privacy_level=PrivacyLevel.PRIVATE,
                local_sufficiency=LocalSufficiency.LOCAL_PRIVATE_BLOCKED,
                recommended_route=RoutingDecision.LOCAL_ONLY,
                needs_local_retrieval=False,
                needs_online_model=False,
                redaction_required=False,
                reason="Guardian unavailable — safe fallback.",
                confidence=0.0,
            )

        privacy_level = analysis.privacy_level
        local_sufficiency = analysis.local_sufficiency

        # Step 4: decide route
        routing = default_route(privacy_level, local_sufficiency)
        if force_route is not None:
            if force_route == RoutingDecision.LOCAL_ONLY:
                # /local-ask always wins — even HIGHLY_PRIVATE content can be answered locally
                routing = RoutingDecision.LOCAL_ONLY
            elif routing not in (RoutingDecision.BLOCKED, RoutingDecision.APPROVAL_REQUIRED):
                # Cannot force GUARDED_ONLINE past a policy block
                routing = force_route

        self._log_query(query_id, query_text, routing, now)
        self._log_classification(query_id, privacy_level, routing, analysis.confidence, now)

        sources: list[str] = []

        routing_detail = {
            "route": routing.value,
            "needs_local_retrieval": analysis.needs_local_retrieval,
            "needs_online_model": analysis.needs_online_model,
            "local_sufficiency": local_sufficiency.value,
            "privacy_level": privacy_level.value,
            "reason": analysis.reason,
            "retrieved_sources": sources,
            "redaction_required": analysis.redaction_required,
            "approval_required": routing == RoutingDecision.APPROVAL_REQUIRED,
        }

        if routing == RoutingDecision.BLOCKED:
            return PipelineResult(
                answer="This query has been blocked — it contains data classified as SECRET.",
                routing=routing,
                privacy_level=privacy_level,
                local_sufficiency=local_sufficiency,
                sources=[],
                query_id=query_id,
                status="blocked",
                routing_detail=routing_detail,
            )

        if routing == RoutingDecision.LOCAL_ONLY:
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, routing, routing_detail)

        if routing == RoutingDecision.APPROVAL_REQUIRED:
            return self._approval_path(query_id, query_text, local_context, privacy_level)

        if routing == RoutingDecision.HYBRID_KNOWLEDGE_ONLY:
            return self._hybrid_path(query_id, query_text, local_context, privacy_level, local_sufficiency, routing_detail)

        # GUARDED_ONLINE
        return self._online_path(query_id, query_text, local_context, privacy_level, local_sufficiency, routing_detail)

    def resume(self, query_id: str, query_text: str) -> "PipelineResult":
        """
        Resume a pending_approval query after the user confirms via POST /ask/:id/approve.
        Re-runs from step 5 (sanitize → Expert call → check → merge → finalize).
        """
        local_context = self._retrieve_local(query_text)
        default_analysis = AnalysisResult(
            privacy_level=PrivacyLevel.HIGHLY_PRIVATE,
            local_sufficiency=LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL,
            recommended_route=RoutingDecision.GUARDED_ONLINE,
            needs_local_retrieval=True,
            needs_online_model=True,
            redaction_required=True,
            reason="Resumed from approval flow.",
            confidence=1.0,
        )
        routing_detail: dict = {
            "route": RoutingDecision.GUARDED_ONLINE.value,
            "needs_local_retrieval": True,
            "needs_online_model": True,
            "local_sufficiency": LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL.value,
            "privacy_level": PrivacyLevel.HIGHLY_PRIVATE.value,
            "reason": "Resumed from approval flow.",
            "retrieved_sources": [],
            "redaction_required": True,
            "approval_required": False,
        }
        return self._online_path(
            query_id, query_text, local_context,
            PrivacyLevel.HIGHLY_PRIVATE,
            LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL,
            routing_detail,
        )

    # ------------------------------------------------------------------
    # Routing paths
    # ------------------------------------------------------------------

    def _local_path(
        self,
        query_id: str,
        query_text: str,
        local_context: str,
        privacy_level: PrivacyLevel,
        local_sufficiency: LocalSufficiency,
        routing: RoutingDecision,
        routing_detail: dict,
    ) -> "PipelineResult":
        answer = self._finalize_local(query_text, local_context, query_id=query_id)
        sources = ["local"]
        routing_detail["retrieved_sources"] = sources
        return PipelineResult(
            answer=answer,
            routing=routing,
            privacy_level=privacy_level,
            local_sufficiency=local_sufficiency,
            sources=sources,
            query_id=query_id,
            routing_detail=routing_detail,
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
        local_sufficiency: LocalSufficiency,
        routing_detail: dict,
    ) -> "PipelineResult":
        # When Guardian says local data is irrelevant to the question, don't
        # include it in the cloud payload — only the question goes online.
        context_for_cloud = (
            "" if local_sufficiency == LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY
            else local_context
        )

        # Step 5: sanitize
        try:
            payload, redaction_map = sanitize(query_text, context_for_cloud, privacy_level, self.guardian)
        except (BlockedError, AssertionError):
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Nothing useful to send — both question and context are empty
        if not payload.user_question.strip() and not payload.sanitized_context.strip():
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Step 6.5: web search — fetch live data when external knowledge is needed
        if local_sufficiency in (
            LocalSufficiency.LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL,
            LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY,
        ):
            try:
                from app.search.tavily_client import search as web_search
                results = web_search(query_text, query_id=query_id)
                if results:
                    payload.web_search_results = results
                    routing_detail["web_search_used"] = True
                else:
                    routing_detail["web_search_used"] = False
            except Exception:
                routing_detail["web_search_used"] = False
        else:
            routing_detail["web_search_used"] = False

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
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Step 8: check response
        is_clean = check(expert_response, redaction_map)
        if not is_clean:
            self._update_leak(query_id)
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Steps 9+10: merge and finalize
        answer = self._finalize_hybrid_merge(query_text, local_context, expert_response, query_id=query_id)
        sources = ["local", "expert"]
        routing_detail["retrieved_sources"] = sources
        return PipelineResult(
            answer=answer,
            routing=RoutingDecision.GUARDED_ONLINE,
            privacy_level=privacy_level,
            local_sufficiency=local_sufficiency,
            sources=sources,
            query_id=query_id,
            routing_detail=routing_detail,
        )

    def _hybrid_path(
        self,
        query_id: str,
        query_text: str,
        local_context: str,
        privacy_level: PrivacyLevel,
        local_sufficiency: LocalSufficiency,
        routing_detail: dict,
    ) -> "PipelineResult":
        """
        HYBRID_KNOWLEDGE_ONLY path: abstract the question, call expert without
        private context, then merge locally.
        """
        # Step 1: build abstract payload (no raw private data sent online)
        try:
            payload, _ = build_hybrid_payload(query_text, local_context, privacy_level, self.guardian)
        except Exception:
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Step 2: call Expert
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
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Step 3: check response (empty redaction map — no private context was sent)
        is_clean = check(expert_response, {})
        if not is_clean:
            self._update_leak(query_id)
            return self._local_path(query_id, query_text, local_context, privacy_level, local_sufficiency, RoutingDecision.LOCAL_ONLY, routing_detail)

        # Step 4: finalize by merging expert reasoning with local context
        answer = self._finalize_hybrid_merge(query_text, local_context, expert_response, query_id=query_id)
        sources = ["local", "expert"]
        routing_detail["retrieved_sources"] = sources
        return PipelineResult(
            answer=answer,
            routing=RoutingDecision.HYBRID_KNOWLEDGE_ONLY,
            privacy_level=privacy_level,
            local_sufficiency=local_sufficiency,
            sources=sources,
            query_id=query_id,
            routing_detail=routing_detail,
        )

    # ------------------------------------------------------------------
    # Guardian helpers
    # ------------------------------------------------------------------

    def _retrieve_local(
        self,
        query_text: str,
        top_k: int = RETRIEVAL_TOP_K,
    ) -> str:
        """
        Retrieve the top-k most similar chunks from the vault by cosine distance.

        No hard distance threshold is applied here — OCR-degraded PDFs and
        short queries routinely produce distances above any reasonable cutoff
        even when the content is genuinely relevant. Relevance filtering is
        delegated to the Guardian's analyze() step: if the Guardian determines
        local context is irrelevant (LOCAL_MISSING_EXTERNAL_ONLY), the caller
        suppresses it before building the cloud payload.

        Returns empty string if the vault is empty or the embedder is unavailable.
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

        if not rows:
            return ""

        parts = [f"[chunk {i + 1}] {row['text']}" for i, row in enumerate(rows)]
        context = "\n\n".join(parts)

        # Hard cap on chars passed to the Guardian for analysis
        if len(context) > EXPERT_MAX_CONTEXT_CHARS:
            context = context[:EXPERT_MAX_CONTEXT_CHARS] + "\n[context truncated]"

        return context

    def _finalize_local(self, query: str, context: str, query_id: str | None = None) -> str:
        if not self.guardian.is_available():
            return context or "No local context available."
        prompt = (
            _LOCAL_FINALIZE_PREFIX
            + f"Query: {query}\n"
            + f"Local context:\n{context or '(none)'}\n\n"
            + "Answer:"
        )
        try:
            return self.guardian.generate(prompt, role="finalizer", query_id=query_id)
        except GuardianUnavailableError:
            return context or "No local context available."

    def _finalize_hybrid_merge(self, query: str, local_context: str, expert_response: str, query_id: str | None = None) -> str:
        prompt = (
            _HYBRID_FINALIZE_PREFIX
            + f"User question: {query}\n\n"
            + f"Local context (private — do not expose directly):\n{local_context or '(none)'}\n\n"
            + f"General reasoning from knowledge base:\n{expert_response}\n\n"
            + "Final answer:"
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
                    payload.route or "guarded-online",
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
