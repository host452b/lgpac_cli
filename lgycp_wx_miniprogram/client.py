"""Read-only HTTP access with bounded retries and sanitized metadata."""

from dataclasses import dataclass
import random
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from lgycp_wx_miniprogram.config import Settings


MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
RETRYABLE_STATUSES = {408, 425, 429}


@dataclass(frozen=True)
class HttpTrace:
    method: str
    scheme: str
    host: str
    path: str
    status_code: int | None
    attempts: int
    elapsed_ms: int


@dataclass(frozen=True)
class FetchedResponse:
    response: Any
    trace: HttpTrace


@dataclass(frozen=True)
class FetchResult:
    payload: Any
    trace: HttpTrace


class ApiError(RuntimeError):
    """A sanitized API failure safe to emit in CI logs."""

    def __init__(self, message: str, trace: HttpTrace | None = None):
        super().__init__(message)
        self.trace = trace


def _trace(
    settings: Settings,
    *,
    status_code: int | None,
    attempts: int,
    started: float,
) -> HttpTrace:
    parsed = urlsplit(settings.api_url)
    return HttpTrace(
        method=settings.api_method,
        scheme=parsed.scheme,
        host=parsed.hostname or "",
        path=parsed.path or "/",
        status_code=status_code,
        attempts=attempts,
        elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
    )


def _retryable(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUSES or status_code >= 500


def _retry_delay(response: Any, attempt: int, jitter: Callable[[], float]) -> float:
    retry_after = str(response.headers.get("Retry-After", "")).strip()
    if retry_after.isdecimal():
        return min(int(retry_after), MAX_RETRY_AFTER_SECONDS)
    return min(2 ** (attempt - 1) + jitter(), 5.0)


def fetch_response(
    settings: Settings,
    session: requests.Session | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> FetchedResponse:
    http = session or requests.Session()
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = http.request(
                settings.api_method,
                settings.api_url,
                headers=settings.api_headers,
                params=settings.api_params,
                json=settings.api_body,
                timeout=settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            if attempt < MAX_ATTEMPTS:
                sleep(min(2 ** (attempt - 1) + jitter(), 5.0))
                continue
            trace = _trace(
                settings, status_code=None, attempts=attempt, started=started
            )
            raise ApiError("course API request failed after retries", trace) from exc

        trace = _trace(
            settings,
            status_code=response.status_code,
            attempts=attempt,
            started=started,
        )
        if _retryable(response.status_code):
            if attempt < MAX_ATTEMPTS:
                sleep(_retry_delay(response, attempt, jitter))
                continue
            raise ApiError(
                f"course API returned HTTP {response.status_code}", trace
            )
        if response.status_code >= 400:
            raise ApiError(
                f"course API returned HTTP {response.status_code}", trace
            )
        return FetchedResponse(response=response, trace=trace)

    raise ApiError("course API failed after retries")


def decode_response(fetched: FetchedResponse) -> FetchResult:
    try:
        payload = fetched.response.json()
    except ValueError as exc:
        raise ApiError("course API returned invalid JSON", fetched.trace) from exc
    return FetchResult(payload=payload, trace=fetched.trace)


def fetch_payload(
    settings: Settings,
    session: requests.Session | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> Any:
    fetched = fetch_response(
        settings, session=session, sleep=sleep, jitter=jitter
    )
    return decode_response(fetched).payload
