"""Approved-field email rendering backed by the repository SMTP layer."""

from html import escape

from lgpac.notify import build_html_email, send_email as shared_send_email
from lgycp_wx_miniprogram.models import Course


def build_email(courses: list[Course]) -> tuple[str, str]:
    subject = f"[临港少年宫] {len(courses)} 门新课程"
    rows = [
        [
            escape(course.title),
            escape(course.price_yuan),
            escape(course.course_type),
            escape(course.published_at.isoformat()),
            escape(course.course_start_date),
            escape(course.course_end_date),
        ]
        for course in courses
    ]
    body = build_html_email(
        title="临港少年宫新课程",
        heading_color="#1f2937",
        table_headers=[
            "课程名",
            "价格（元）",
            "类型",
            "上架时间",
            "课程开始",
            "课程结束",
        ],
        table_rows=rows,
    )
    return subject, body


def send_courses(courses: list[Course], settings: object | None = None) -> bool:
    """Send one aggregate email; settings remains optional for call compatibility."""
    if not courses:
        return True
    subject, body = build_email(courses)
    return shared_send_email(subject, body, raise_on_error=True)
