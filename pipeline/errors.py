class ConsistencyCheckError(ValueError):
    """Raised when generated data contradicts established pipeline data or policy."""


class ScenarioGenerationFallbackError(RuntimeError):
    """Raised when scenario generation exhausts retries without a safe fallback."""


def is_non_retryable_provider_error(error: Exception) -> bool:
    """Identify provider failures that cannot recover through immediate retries."""
    if getattr(error, "code", None) == "insufficient_quota":
        return True

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested_error = body.get("error")
        if body.get("code") == "insufficient_quota":
            return True
        if (
            isinstance(nested_error, dict)
            and nested_error.get("code") == "insufficient_quota"
        ):
            return True

    message = str(error)
    return "'code': 'insufficient_quota'" in message or (
        '"code": "insufficient_quota"' in message
    )
