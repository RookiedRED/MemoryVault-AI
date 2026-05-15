from enum import Enum


class PrivacyLevel(str, Enum):
    PUBLIC = "PUBLIC"
    LOW_SENSITIVE = "LOW_SENSITIVE"
    PRIVATE = "PRIVATE"
    HIGHLY_PRIVATE = "HIGHLY_PRIVATE"
    SECRET = "SECRET"


class LocalSufficiency(str, Enum):
    LOCAL_SUFFICIENT = "LOCAL_SUFFICIENT"
    LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL = "LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL"
    LOCAL_MISSING_EXTERNAL_ONLY = "LOCAL_MISSING_EXTERNAL_ONLY"
    LOCAL_PRIVATE_BLOCKED = "LOCAL_PRIVATE_BLOCKED"


class RoutingDecision(str, Enum):
    LOCAL_ONLY = "local-only"
    GUARDED_ONLINE = "guarded-online"
    HYBRID_KNOWLEDGE_ONLY = "hybrid-knowledge-only"
    APPROVAL_REQUIRED = "approval-required"
    BLOCKED = "blocked"


def default_route(level: PrivacyLevel, sufficiency: LocalSufficiency) -> RoutingDecision:
    """Return the default routing decision for a given privacy level and local sufficiency."""
    if level == PrivacyLevel.SECRET:
        return RoutingDecision.BLOCKED
    if sufficiency in (LocalSufficiency.LOCAL_SUFFICIENT, LocalSufficiency.LOCAL_PRIVATE_BLOCKED):
        return RoutingDecision.LOCAL_ONLY
    if sufficiency == LocalSufficiency.LOCAL_MISSING_EXTERNAL_ONLY:
        return RoutingDecision.GUARDED_ONLINE
    # LOCAL_INSUFFICIENT_EXTERNAL_HELPFUL — route by privacy level
    if level == PrivacyLevel.HIGHLY_PRIVATE:
        return RoutingDecision.APPROVAL_REQUIRED
    if level == PrivacyLevel.PRIVATE:
        return RoutingDecision.HYBRID_KNOWLEDGE_ONLY
    return RoutingDecision.GUARDED_ONLINE  # PUBLIC or LOW_SENSITIVE
