from enum import Enum


class PrivacyLevel(str, Enum):
    PUBLIC = "PUBLIC"
    LOW_SENSITIVE = "LOW_SENSITIVE"
    PRIVATE = "PRIVATE"
    HIGHLY_PRIVATE = "HIGHLY_PRIVATE"
    SECRET = "SECRET"


class RoutingDecision(str, Enum):
    LOCAL_ONLY = "local-only"
    GUARDED_ONLINE = "guarded-online"
    APPROVAL_REQUIRED = "approval-required"
    BLOCKED = "blocked"


# Default routing table: privacy level → routing decision.
# LOCAL_ONLY and GUARDED_ONLINE can be overridden by force_route (e.g. /local-ask).
# APPROVAL_REQUIRED and BLOCKED are policy-enforced and cannot be overridden.
_ROUTING_TABLE: dict[PrivacyLevel, RoutingDecision] = {
    PrivacyLevel.PUBLIC: RoutingDecision.GUARDED_ONLINE,
    PrivacyLevel.LOW_SENSITIVE: RoutingDecision.GUARDED_ONLINE,
    PrivacyLevel.PRIVATE: RoutingDecision.GUARDED_ONLINE,
    PrivacyLevel.HIGHLY_PRIVATE: RoutingDecision.APPROVAL_REQUIRED,
    PrivacyLevel.SECRET: RoutingDecision.BLOCKED,
}


def default_route(level: PrivacyLevel) -> RoutingDecision:
    """Return the default routing decision for a given privacy level."""
    return _ROUTING_TABLE[level]
