class ConsistencyCheckError(ValueError):
    """Raised when generated data contradicts established pipeline data or policy."""


class ScenarioGenerationFallbackError(RuntimeError):
    """Raised when scenario generation exhausts retries without a safe fallback."""
