"""
shared notification layer.
single implementation for email (SMTP) and webhook (dingtalk/slack/generic).
all env vars are read once at call time, never hardcoded.
"""
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Optional, List, Dict
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("lgpac.notify")


# ------------------------------------------------------------------ #
# email
# ------------------------------------------------------------------ #

def send_email(subject: str, html_body: str) -> bool:
    """
    send an HTML email.
    reads LGPAC_NOTIFY_EMAIL, LGPAC_SMTP_USER, LGPAC_SMTP_PASS,
    LGPAC_SMTP_SERVER (default smtp.qq.com), LGPAC_SMTP_PORT (default 465).
    returns True on success, False on skip/failure.
    """
    to_addr = os.environ.get("LGPAC_NOTIFY_EMAIL", "").strip()
    smtp_user = os.environ.get("LGPAC_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("LGPAC_SMTP_PASS", "").strip()
    smtp_server = os.environ.get("LGPAC_SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("LGPAC_SMTP_PORT", "465"))

    if not to_addr or not smtp_user or not smtp_pass:
        logger.debug("email: credentials not configured, skipped")
        return False

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_addr], msg.as_string())
        logger.info("email: sent successfully")
        return True
    except Exception as e:
        logger.warning(f"email: send failed - {type(e).__name__}")
        return False


# ------------------------------------------------------------------ #
# webhook
# ------------------------------------------------------------------ #

def send_webhook(text: str, webhook_url: Optional[str] = None):
    """
    send alert via webhook. auto-detects platform by URL:
      dingtalk, slack, generic.
    """
    url = webhook_url or os.environ.get("LGPAC_WEBHOOK_URL", "")
    if not url:
        return

    body = _build_webhook_payload(url, text)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urlopen(req, timeout=10) as resp:
            logger.info(f"webhook sent: {resp.status}")
    except URLError as e:
        logger.warning(f"webhook failed: {e}")


def _build_webhook_payload(url: str, text: str) -> Dict:
    if "dingtalk.com" in url:
        return {"msgtype": "text", "text": {"content": text}}
    if "hooks.slack.com" in url:
        return {"text": text}
    return {"text": text, "content": text}


# ------------------------------------------------------------------ #
# HTML helpers
# ------------------------------------------------------------------ #

_CELL_STYLE = 'padding:6px 8px;'
_HEADER_STYLE = 'padding:6px 8px;text-align:left;'
_TABLE_STYLE = 'border-collapse:collapse;width:100%;font-size:14px;'


def build_html_email(title: str, heading_color: str, table_headers: List[str], table_rows: List[List[str]]) -> str:
    """
    build a complete HTML email body with a heading + table.
    each row is a list of cell HTML strings (can contain links, styling).
    """
    header_cells = "".join(f'<th style="{_HEADER_STYLE}">{h}</th>' for h in table_headers)
    body_rows = ""
    for row in table_rows:
        cells = "".join(f'<td style="{_CELL_STYLE}">{cell}</td>' for cell in row)
        body_rows += f"<tr>{cells}</tr>"

    return (
        f'<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:0 auto;">'
        f'<h2 style="color:{heading_color};">{title}</h2>'
        f'<table style="{_TABLE_STYLE}">'
        f'<tr style="background:#f6f8fa;">{header_cells}</tr>'
        f'{body_rows}'
        f'</table></body></html>'
    )
