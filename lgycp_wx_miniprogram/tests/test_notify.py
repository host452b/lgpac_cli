from datetime import datetime

from lgycp_wx_miniprogram.models import Course, SHANGHAI
import lgycp_wx_miniprogram.notify as notify_module


def sample_course(**overrides) -> Course:
    values = {
        "course_id": "course-001",
        "title": "少儿绘画",
        "published_at": datetime(2026, 7, 6, 10, 0, tzinfo=SHANGHAI),
        "price_yuan": "800.00",
        "course_type": "美术",
        "course_start_date": "2026-07-15",
        "course_end_date": "2026-08-15",
    }
    values.update(overrides)
    return Course(**values)


def test_build_email_contains_only_approved_course_fields():
    subject, body = notify_module.build_email([sample_course()])

    assert subject == "[临港少年宫] 1 门新课程"
    for expected in [
        "少儿绘画",
        "800.00",
        "美术",
        "2026-07-06T10:00:00+08:00",
        "2026-07-15",
        "2026-08-15",
    ]:
        assert expected in body
    for forbidden in [
        "coursePicUrl",
        "image_url",
        "registration_time",
        "临港校区",
    ]:
        assert forbidden not in body


def test_build_email_escapes_html():
    _, body = notify_module.build_email(
        [sample_course(title="<script>alert(1)</script>")]
    )

    assert "<script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_send_courses_skips_empty_list(monkeypatch):
    monkeypatch.setattr(
        notify_module,
        "shared_send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("email must not be sent")
        ),
        raising=False,
    )

    assert notify_module.send_courses([]) is True


def test_send_courses_uses_shared_detailed_delivery(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notify_module,
        "shared_send_email",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        raising=False,
    )

    assert notify_module.send_courses([sample_course()]) is True
    assert len(calls) == 1
    assert calls[0][0][0] == "[临港少年宫] 1 门新课程"
    assert "少儿绘画" in calls[0][0][1]
    assert calls[0][1] == {"raise_on_error": True}
