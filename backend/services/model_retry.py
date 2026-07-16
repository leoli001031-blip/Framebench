from __future__ import annotations

import httpx


RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class ModelRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def is_retryable_model_error(error: Exception) -> bool:
    if isinstance(error, ModelRequestError):
        return error.status_code in RETRYABLE_STATUS_CODES
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError))


def model_retry_delay(error: Exception, attempt: int) -> float:
    if isinstance(error, ModelRequestError) and error.retry_after is not None:
        return min(error.retry_after, 30.0)
    return min(12.0, float(2 ** attempt))
