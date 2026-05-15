from abc import ABC, abstractmethod


class ExpertModelClient(ABC):
    """
    Abstract interface for the Online Expert Model.

    The concrete implementation (OpenAIExpertClient) is injected by the pipeline.
    Swapping providers = swapping the implementation class; the pipeline is unchanged.
    """

    @abstractmethod
    def call(self, payload) -> str:
        """
        Send a SanitizedPayload to the Expert Model.
        Returns the Expert's text response.
        Raises on network failure or API error.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the Expert Model can be reached (e.g. API key is set)."""
        ...
