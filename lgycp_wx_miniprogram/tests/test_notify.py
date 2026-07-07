from datetime import datetime
import smtplib

from lgycp_wx_miniprogram.models import Course, SHANGHAI
from lgycp_wx_miniprogram.notify import build_message, send_courses
from lgycp_wx_miniprogram.tests.test_models import settings


def sample_course(**overrides) -> Course:
    values = {
        "course_id": "course-001",
        "title": "少儿绘画",
        "published_at": datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI),
        "campus": "临港校区",
        "term": "暑期",
        "schedule": "周六 10:00",
        "price": "800",
        "remaining": "5",
        "detail_url": "https://example.invalid/course?id=1&from=mail",
    }
    values.update(overrides)
    return Course(**values)


def html_body(message) -> str:
    return message.get_payload(decode=True).decode("utf-8")


def test_build_message_contains_count_and_course_fields():
    message = build_message([sample_course()], settings())
    body = html_body(message)

    assert message["Subject"] == "[临港少年宫] 1 门新课程"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    for expected in ["少儿绘画", "临港校区", "暑期", "周六 10:00", "800", "5"]:
        assert expected in body
    assert 'href="https://example.invalid/course?id=1&amp;from=mail"' in body


def test_build_message_escapes_html_and_rejects_unsafe_link_scheme():
    message = build_message(
        [
            sample_course(
                title="<script>alert(1)</script>", detail_url="javascript:alert(1)"
            )
        ],
        settings(),
    )
    body = html_body(message)

    assert "<script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "javascript:" not in body


def test_send_courses_skips_smtp_for_empty_list(monkeypatch):
    def unexpected_smtp(*args, **kwargs):
        raise AssertionError("SMTP must not be opened")

    monkeypatch.setattr("lgycp_wx_miniprogram.notify.smtplib.SMTP_SSL", unexpected_smtp)

    assert send_courses([], settings()) is True


def test_send_courses_logs_in_and_sends_one_message(monkeypatch):
    instances = []

    class FakeSmtp:
        def __init__(self, server, port, timeout):
            self.connection = (server, port, timeout)
            self.login_args = None
            self.sendmail_args = None
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            self.login_args = (user, password)

        def sendmail(self, sender, recipients, message):
            self.sendmail_args = (sender, recipients, message)

    monkeypatch.setattr("lgycp_wx_miniprogram.notify.smtplib.SMTP_SSL", FakeSmtp)

    assert send_courses([sample_course()], settings()) is True
    assert len(instances) == 1
    assert instances[0].connection == ("smtp.qq.com", 465, 15)
    assert instances[0].login_args == ("from@example.com", "secret")
    assert instances[0].sendmail_args[0:2] == (
        "from@example.com",
        ["to@example.com"],
    )


def test_send_courses_returns_false_on_smtp_failure(monkeypatch):
    def fail_smtp(*args, **kwargs):
        raise smtplib.SMTPException("delivery failed")

    monkeypatch.setattr("lgycp_wx_miniprogram.notify.smtplib.SMTP_SSL", fail_smtp)

    assert send_courses([sample_course()], settings()) is False
