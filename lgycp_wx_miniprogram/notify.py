"""HTML email rendering and SMTP delivery for new courses."""

from email.mime.text import MIMEText
from html import escape
import smtplib
from urllib.parse import urlsplit

from lgycp_wx_miniprogram.config import Settings
from lgycp_wx_miniprogram.models import Course


def _safe_link(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return escape(url, quote=True)


def build_message(courses: list[Course], settings: Settings) -> MIMEText:
    rows = []
    for course in courses:
        title = escape(course.title)
        link = _safe_link(course.detail_url)
        if link:
            title = f'<a href="{link}">{title}</a>'
        values = [
            title,
            escape(course.published_at.isoformat()),
            escape(course.campus),
            escape(course.term),
            escape(course.schedule),
            escape(course.price),
            escape(course.remaining),
        ]
        cells = "".join(f'<td style="padding:6px 8px">{value}</td>' for value in values)
        rows.append(f"<tr>{cells}</tr>")

    body = (
        '<html><body style="font-family:-apple-system,Arial,sans-serif">'
        "<h2>临港少年宫新课程</h2>"
        '<table style="border-collapse:collapse;width:100%">'
        "<tr><th>课程</th><th>上架时间</th><th>校区</th><th>学期</th>"
        "<th>上课时间</th><th>价格</th><th>剩余名额</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )
    message = MIMEText(body, "html", "utf-8")
    message["Subject"] = f"[临港少年宫] {len(courses)} 门新课程"
    message["From"] = settings.smtp_user
    message["To"] = settings.notify_email
    return message


def send_courses(courses: list[Course], settings: Settings) -> bool:
    if not courses:
        return True
    message = build_message(courses, settings)
    try:
        with smtplib.SMTP_SSL(
            settings.smtp_server, settings.smtp_port, timeout=15
        ) as smtp:
            smtp.login(settings.smtp_user, settings.smtp_pass)
            smtp.sendmail(
                settings.smtp_user, [settings.notify_email], message.as_string()
            )
    except (OSError, smtplib.SMTPException):
        return False
    return True
