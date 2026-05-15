from dataclasses import dataclass, field


@dataclass
class PrivacyPolicy:
    default_external_call: str = "deny"
    raw_personal_data_online: bool = False
    identity_mapping_online: bool = False
    audit_all_online_calls: bool = True
    preview_sensitive_payloads: bool = True
    final_answer_checked_locally: bool = True


DEFAULT_POLICY = PrivacyPolicy()


class PolicyManager:
    """Runtime-mutable policy. Starts from DEFAULT_POLICY; updated via PUT /privacy/policy."""

    def __init__(self) -> None:
        self._policy = PrivacyPolicy()

    def get(self) -> PrivacyPolicy:
        return self._policy

    def update(self, **kwargs) -> PrivacyPolicy:
        for key, value in kwargs.items():
            if not hasattr(self._policy, key):
                raise ValueError(f"Unknown policy key: {key!r}")
            setattr(self._policy, key, value)
        return self._policy

    def reset(self) -> None:
        self._policy = PrivacyPolicy()


# Module-level singleton used by the pipeline.
policy_manager = PolicyManager()
