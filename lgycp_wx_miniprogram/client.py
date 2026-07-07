"""Read-only HTTP access to the configured course endpoint."""

import time
from typing import Any

import requests

from lgycp_wx_miniprogram.config import Settings


class ApiError(RuntimeError):
    """A sanitized API failure safe to emit in CI logs."""


def fetch_payload(settings: Settings, session: requests.Session | None = None) -> Any:
    http = session or requests.Session()
    for attempt in range(3):
        try:
            response = http.request(
                settings.api_method,
                settings.api_url,
                headers=settings.api_headers,
                json=settings.api_body,
                timeout=settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise ApiError("course API request failed after retries") from exc

        if response.status_code >= 500 and attempt < 2:
            time.sleep(2**attempt)
            continue
        if response.status_code >= 400:
            raise ApiError(f"course API returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("course API returned invalid JSON") from exc

    raise ApiError("course API failed after retries")
