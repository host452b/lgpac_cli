from dataclasses import replace

import pytest
import requests

from lgycp_wx_miniprogram.client import ApiError, fetch_payload
from lgycp_wx_miniprogram.tests.test_models import settings


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("lgycp_wx_miniprogram.client.time.sleep", lambda _: None)


def test_fetch_payload_sends_configured_request():
    configured = replace(
        settings(),
        api_method="POST",
        api_headers={"Authorization": "redacted"},
        api_params={"pageNo": "1"},
        api_body={"page": 1},
        timeout_seconds=9,
    )
    session = FakeSession([FakeResponse(payload={"data": {"list": [1]}})])

    payload = fetch_payload(configured, session=session)

    assert payload == {"data": {"list": [1]}}
    assert session.calls == [
        (
            "POST",
            "https://example.invalid/courses",
            {
                "headers": {"Authorization": "redacted"},
                "params": {"pageNo": "1"},
                "json": {"page": 1},
                "timeout": 9,
            },
        )
    ]


def test_fetch_payload_retries_connection_failure_then_succeeds():
    session = FakeSession(
        [requests.ConnectionError("offline"), FakeResponse(payload={"ok": True})]
    )

    assert fetch_payload(settings(), session=session) == {"ok": True}
    assert len(session.calls) == 2


def test_fetch_payload_retries_server_errors_at_most_twice():
    session = FakeSession([FakeResponse(500), FakeResponse(502), FakeResponse(503)])

    with pytest.raises(ApiError, match="HTTP 503"):
        fetch_payload(settings(), session=session)

    assert len(session.calls) == 3


def test_fetch_payload_does_not_retry_client_error():
    session = FakeSession([FakeResponse(401)])

    with pytest.raises(ApiError, match="HTTP 401"):
        fetch_payload(settings(), session=session)

    assert len(session.calls) == 1


def test_fetch_payload_retries_connection_failure_only_three_attempts():
    session = FakeSession([requests.Timeout("token-value")] * 3)

    with pytest.raises(ApiError, match="after retries") as error:
        fetch_payload(settings(), session=session)

    assert len(session.calls) == 3
    assert "token-value" not in str(error.value)


def test_fetch_payload_rejects_invalid_json_without_headers_in_error():
    configured = replace(settings(), api_headers={"Authorization": "token-value"})
    session = FakeSession([FakeResponse(json_error=ValueError("token-value"))])

    with pytest.raises(ApiError, match="invalid JSON") as error:
        fetch_payload(configured, session=session)

    assert "token-value" not in str(error.value)
