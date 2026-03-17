"""
email generator — multipart HTML + plain text digest.
adapted from kylejohnston/rss-email-digest.
"""
import html
import smtplib
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def generate_plain_text(feed_results):
    """generate plain text email body."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"RSS Digest — {date_str}", ""]

    sorted_feeds = sorted(feed_results, key=lambda f: f["name"].lower())
    with_posts = [f for f in sorted_feeds if f["posts"]]
    failed = [f for f in sorted_feeds if f["status"] == "error"]

    if with_posts:
        for feed in with_posts:
            lines.append(feed["name"])
            for post in feed["posts"]:
                title = html.unescape(post["title"])
                lines.append(f"  - {title}")
                lines.append(f"    {post['link']}")
                if post["excerpt"]:
                    lines.append(f"    {html.unescape(post['excerpt'])}")
            lines.append("")
    else:
        lines.append("no updates in this period.")
        lines.append("")

    lines.append("---")
    lines.append(f"{len(with_posts)} of {len(feed_results)} feeds had updates")
    if failed:
        lines.append(f"{len(failed)} feeds failed:")
        for f in failed:
            lines.append(f"  - {f['name']}: {f.get('error', 'unknown')}")

    return "\n".join(lines)


def generate_html(feed_results):
    """generate HTML email body."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sorted_feeds = sorted(feed_results, key=lambda f: f["name"].lower())
    with_posts = [f for f in sorted_feeds if f["posts"]]
    failed = [f for f in sorted_feeds if f["status"] == "error"]

    parts = [
        '<html><head><style>',
        'body{font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:#222;max-width:680px;margin:0 auto;padding:16px;}',
        'a{color:#0969da;text-decoration:none;}',
        'a:hover{text-decoration:underline;}',
        'h1{font-size:20px;border-bottom:1px solid #d0d7de;padding-bottom:8px;}',
        'h2{font-size:17px;margin-top:24px;}',
        'h2 a{font-size:17px;}',
        '.post{margin:8px 0 12px 0;}',
        '.post a{font-size:15px;font-weight:600;}',
        '.excerpt{color:#656d76;font-size:14px;margin-top:2px;}',
        '.summary{margin-top:32px;padding-top:12px;border-top:1px solid #d0d7de;color:#656d76;font-size:13px;}',
        '.error{color:#cf222e;}',
        '</style></head><body>',
        f'<h1>RSS Digest — {date_str}</h1>',
    ]

    if with_posts:
        for feed in with_posts:
            name_escaped = html.escape(feed["name"])
            site_url = feed.get("site_url", "")
            if site_url:
                parts.append(f'<h2><a href="{html.escape(site_url)}">{name_escaped}</a></h2>')
            else:
                parts.append(f'<h2>{name_escaped}</h2>')

            for post in feed["posts"]:
                title = html.escape(html.unescape(post["title"]))
                link = html.escape(post["link"])
                parts.append('<div class="post">')
                parts.append(f'<a href="{link}">{title}</a>')
                if post["excerpt"]:
                    excerpt = html.escape(html.unescape(post["excerpt"]))
                    parts.append(f'<div class="excerpt">{excerpt}</div>')
                parts.append('</div>')
    else:
        parts.append('<p>no updates in this period.</p>')

    parts.append('<div class="summary">')
    parts.append(f'<b>{len(with_posts)}</b> of {len(feed_results)} feeds had updates')
    if failed:
        parts.append(f'<br><span class="error">{len(failed)} feeds failed:</span>')
        parts.append('<ul style="margin:4px 0;">')
        for f in failed:
            name = html.escape(f["name"])
            err = html.escape(f.get("error", "unknown"))
            parts.append(f'<li>{name} — <span class="error">{err}</span></li>')
        parts.append('</ul>')
    parts.append('</div>')
    parts.append('</body></html>')

    return "\n".join(parts)


def build_message(feed_results, from_email, to_email):
    """create multipart MIME message (plain + HTML)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"RSS Digest — {date_str}"
    msg["From"] = from_email
    msg["To"] = to_email

    msg.attach(MIMEText(generate_plain_text(feed_results), "plain", "utf-8"))
    msg.attach(MIMEText(generate_html(feed_results), "html", "utf-8"))

    return msg


def send_email(msg, smtp_server, smtp_port, smtp_user, smtp_pass):
    """send email via SMTP. supports SSL (465) and STARTTLS (587)."""
    logger.info(f"sending via {smtp_server}:{smtp_port}")

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)

        logger.info(f"email sent to {msg['To']}")
    except Exception as e:
        logger.error(f"email failed: {type(e).__name__}: {e}")
        raise
