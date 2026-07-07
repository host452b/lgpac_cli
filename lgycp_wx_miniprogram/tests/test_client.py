from dataclasses import replace

import pytest
import requests

import lgycp_wx_miniprogram.client as client_module
from lgycp_wx_miniprogram.client import ApiError, fetch_payload
from lgycp_wx_miniprogram.tests.test_models import settings


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.headers = headers or {}

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

    payload = fetch_payload(
        configured, session=session, sleep=lambda _: None, jitter=lambda: 0
    )

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

    payload = fetch_payload(
        settings(), session=session, sleep=lambda _: None, jitter=lambda: 0
    )

    assert payload == {"ok": True}
    assert len(session.calls) == 2


def test_fetch_payload_retries_server_errors_at_most_twice():
    session = FakeSession([FakeResponse(500), FakeResponse(502), FakeResponse(503)])

    with pytest.raises(ApiError, match="HTTP 503"):
        fetch_payload(
            settings(), session=session, sleep=lambda _: None, jitter=lambda: 0
        )

    assert len(session.calls) == 3


def test_fetch_payload_does_not_retry_client_error():
    session = FakeSession([FakeResponse(401)])

    with pytest.raises(ApiError, match="HTTP 401"):
        fetch_payload(
            settings(), session=session, sleep=lambda _: None, jitter=lambda: 0
        )

    assert len(session.calls) == 1


def test_fetch_payload_retries_connection_failure_only_three_attempts():
    session = FakeSession([requests.Timeout("token-value")] * 3)

    with pytest.raises(ApiError, match="after retries") as error:
        fetch_payload(
            settings(), session=session, sleep=lambda _: None, jitter=lambda: 0
        )

    assert len(session.calls) == 3
    assert "token-value" not in str(error.value)


def test_fetch_payload_rejects_invalid_json_without_headers_in_error():
    configured = replace(settings(), api_headers={"Authorization": "token-value"})
    session = FakeSession([FakeResponse(json_error=ValueError("token-value"))])

    with pytest.raises(ApiError, match="invalid JSON") as error:
        fetch_payload(
            configured, session=session, sleep=lambda _: None, jitter=lambda: 0
        )

    assert "token-value" not in str(error.value)


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_fetch_response_retries_transient_statuses(status):
    session = FakeSession([FakeResponse(status), FakeResponse(payload={"ok": True})])
    sleeps = []

    fetched = client_module.fetch_response(
        settings(), session=session, sleep=sleeps.append, jitter=lambda: 0.25
    )

    assert fetched.trace.status_code == 200
    assert fetched.trace.attempts == 2
    assert len(session.calls) == 2
    assert sleeps == [1.25]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_fetch_response_does_not_retry_permanent_client_errors(status):
    session = FakeSession([FakeResponse(status)])

    with pytest.raises(ApiError, match=f"HTTP {status}"):
        client_module.fetch_response(
            settings(), session=session, sleep=lambda _: None, jitter=lambda: 0
        )

    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [("12", 12), ("90", 30), ("invalid", 1.25)],
)
def test_fetch_response_honors_bounded_retry_after(retry_after, expected):
    session = FakeSession(
        [
            FakeResponse(429, headers={"Retry-After": retry_after}),
            FakeResponse(payload={"ok": True}),
        ]
    )
    sleeps = []

    client_module.fetch_response(
        settings(), session=session, sleep=sleeps.append, jitter=lambda: 0.25
    )

    assert sleeps == [expected]


def test_fetch_trace_strips_query_headers_and_body():
    configured = replace(
        settings(),
        api_url="https://example.invalid/courses?token=query-canary",
        api_headers={"Authorization": "header-canary"},
        api_body={"secret": "body-canary"},
    )
    session = FakeSession([FakeResponse(payload={"ok": True})])

    fetched = client_module.fetch_response(
        configured, session=session, sleep=lambda _: None, jitter=lambda: 0
    )
    rendered = repr(fetched.trace)

    assert fetched.trace.scheme == "https"
    assert fetched.trace.host == "example.invalid"
    assert fetched.trace.path == "/courses"
    assert fetched.trace.method == "GET"
    assert fetched.trace.status_code == 200
    assert fetched.trace.attempts == 1
    for canary in ["query-canary", "header-canary", "body-canary"]:
        assert canary not in rendered


def test_decode_response_reports_invalid_json_safely():
    fetched = client_module.fetch_response(
        settings(),
        session=FakeSession([FakeResponse(json_error=ValueError("body-canary"))]),
        sleep=lambda _: None,
        jitter=lambda: 0,
    )

    with pytest.raises(ApiError, match="invalid JSON") as error:
        client_module.decode_response(fetched)

    assert "body-canary" not in str(error.value)
