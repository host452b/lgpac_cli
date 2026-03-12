"""
ticket availability monitor.
filters shows by price threshold, tracks stock changes,
and generates alerts for affordable in-stock tickets.
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from lgpac.models import Show, SeatPlan

logger = logging.getLogger("lgpac.monitor")

HISTORY_FILE = "monitor_history.json"


@dataclass
class TicketAlert:
    show_id: str
    show_name: str
    show_date: str
    category: str
    venue: str
    plans: List[Dict[str, Any]] = field(default_factory=list)
    first_seen: str = ""
    status: str = ""  # "new" | "available" | "sold_out" | "back_in_stock"


def analyze_shows(
    shows: List[Show],
    max_price: float = 120.0,
) -> List[TicketAlert]:
    """
    scan all shows for affordable tickets.
    returns alerts sorted by show date.
    """
    history = _load_history()
    now = datetime.now(timezone.utc).isoformat()
    alerts = []

    for show in shows:
        affordable_plans = _find_affordable_plans(show, max_price)
        if not affordable_plans:
            continue

        in_stock = [p for p in affordable_plans if p["available"]]

        prev = history.get(show.show_id)

        if not prev:
            status = "new" if in_stock else "sold_out"
            first_seen = now
        elif prev.get("had_stock") and not in_stock:
            status = "sold_out"
            first_seen = prev.get("first_seen", now)
        elif not prev.get("had_stock") and in_stock:
            status = "back_in_stock"
            first_seen = prev.get("first_seen", now)
        elif in_stock:
            status = "available"
            first_seen = prev.get("first_seen", now)
        else:
            status = "sold_out"
            first_seen = prev.get("first_seen", now)

        alert = TicketAlert(
            show_id=show.show_id,
            show_name=show.name,
            show_date=show.show_date,
            category=show.category.display_name if show.category else "",
            venue=show.venue_name,
            plans=affordable_plans,
            first_seen=first_seen,
            status=status,
        )
        alerts.append(alert)

        # update history
        history[show.show_id] = {
            "name": show.name,
            "first_seen": first_seen,
            "last_checked": now,
            "had_stock": len(in_stock) > 0,
            "plan_count": len(affordable_plans),
        }

    _save_history(history)

    alerts.sort(key=lambda a: a.show_date)
    return alerts


def _find_affordable_plans(show: Show, max_price: float) -> List[Dict[str, Any]]:
    """collect all seat plans under the price threshold with real stock status."""
    results = []
    for session in show.sessions:
        for plan in session.seat_plans:
            if plan.original_price <= max_price and plan.original_price > 0:
                results.append({
                    "session": session.name,
                    "name": plan.name,
                    "price": plan.original_price,
                    "is_combo": plan.is_combo,
                    "is_stop_sale": plan.is_stop_sale,
                    "can_buy_count": plan.can_buy_count,
                    "available": plan.truly_available,
                })
    return results


def format_alerts_text(alerts: List[TicketAlert], max_price: float) -> str:
    """format alerts as plain text for console or notification."""
    if not alerts:
        return f"no tickets found under ¥{max_price:.0f}"

    lines = [f"🎫 tickets under ¥{max_price:.0f}\n"]

    in_stock = [a for a in alerts if a.status in ("new", "available", "back_in_stock")]
    sold_out = [a for a in alerts if a.status == "sold_out"]

    if in_stock:
        for a in in_stock:
            plans_in_stock = [p for p in a.plans if p["available"]]
            prices = sorted(set(p["price"] for p in plans_in_stock))
            price_str = "/".join(f"¥{p:.0f}" for p in prices)

            tag = ""
            if a.status == "new":
                tag = " 🆕"
            elif a.status == "back_in_stock":
                tag = " 🔄"

            lines.append(
                f"  ✅ [{a.category}] {a.show_name}{tag}\n"
                f"     {a.show_date} | {price_str} | "
                f"{len(plans_in_stock)} plan(s) in stock\n"
                f"     first seen: {a.first_seen[:10]}"
            )

    if sold_out:
        lines.append("")
        for a in sold_out:
            prices = sorted(set(p["price"] for p in a.plans))
            price_str = "/".join(f"¥{p:.0f}" for p in prices)
            lines.append(
                f"  ❌ [{a.category}] {a.show_name}\n"
                f"     {a.show_date} | {price_str} | SOLD OUT"
            )

    return "\n".join(lines)


def format_alerts_markdown(alerts: List[TicketAlert], max_price: float) -> str:
    """format alerts as markdown for RSS.md."""
    if not alerts:
        return f"*no tickets under ¥{max_price:.0f}*\n"

    lines = [f"### 🎫 Tickets Under ¥{max_price:.0f}", ""]
    lines.append("| Status | Category | Name | Date | Prices | Stock | Since |")
    lines.append("|--------|----------|------|------|--------|-------|-------|")

    for a in alerts:
        in_stock_plans = [p for p in a.plans if p["available"]]
        prices = sorted(set(p["price"] for p in a.plans))
        price_str = " / ".join(f"¥{p:.0f}" for p in prices)
        stock_str = f"{len(in_stock_plans)}/{len(a.plans)}"

        icon = {"new": "🆕", "available": "✅", "back_in_stock": "🔄", "sold_out": "❌"}.get(a.status, "?")
        name = a.show_name.replace("|", "/")
        since = a.first_seen[:10]

        lines.append(f"| {icon} | {a.category} | {name} | {a.show_date} | {price_str} | {stock_str} | {since} |")

    lines.append("")
    return "\n".join(lines)


def send_webhook(text: str, webhook_url: Optional[str] = None):
    """
    send alert via webhook. auto-detects platform by URL:
      - feishu/lark: open.feishu.cn or open.larksuite.com
      - dingtalk: oapi.dingtalk.com
      - slack: hooks.slack.com
      - generic: plain JSON {"text": "..."}
    set LGPAC_WEBHOOK_URL env var or pass directly.
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
    """format payload for the target platform."""
    if "feishu.cn" in url or "larksuite.com" in url:
        return {
            "msg_type": "text",
            "content": {"text": text},
        }
    if "dingtalk.com" in url:
        return {
            "msgtype": "text",
            "text": {"content": text},
        }
    if "hooks.slack.com" in url:
        return {"text": text}
    # generic fallback
    return {"text": text, "content": text}


def send_email_alert(alerts: List[TicketAlert], max_price: float, index_md_path: str = "docs_lgpac/index.md") -> bool:
    """
    send email when:
      1. a NEW show appears with affordable tickets in stock
      2. an OLD show's cheapest tier comes BACK IN STOCK
    attaches the full index.md dashboard as HTML for easy reading.
    """
    import smtplib
    from email.mime.text import MIMEText

    to_addr = os.environ.get("LGPAC_NOTIFY_EMAIL", "").strip()
    if not to_addr:
        logger.debug("email: LGPAC_NOTIFY_EMAIL not set, skipped")
        return False

    worth_emailing = [a for a in alerts if a.status in ("new", "back_in_stock")]
    if not worth_emailing:
        logger.info("email: no new/restocked shows, skipped")
        return False

    new_shows = [a for a in worth_emailing if a.status == "new"]
    restocked = [a for a in worth_emailing if a.status == "back_in_stock"]

    # build subject
    subject_parts = []
    if new_shows:
        subject_parts.append(f"{len(new_shows)} new")
    if restocked:
        subject_parts.append(f"{len(restocked)} restocked")
    subject = f"[lgpac] {' + '.join(subject_parts)} (under ¥{max_price:.0f})"

    # build HTML body: alert summary + full dashboard
    html = _build_email_html(new_shows, restocked, max_price, index_md_path)

    smtp_server = os.environ.get("LGPAC_SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("LGPAC_SMTP_PORT", "465"))
    smtp_user = os.environ.get("LGPAC_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("LGPAC_SMTP_PASS", "").strip()

    if not smtp_user or not smtp_pass:
        logger.warning("email: SMTP credentials not set, skipped")
        return False

    msg = MIMEText(html, "html", "utf-8")
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


def _build_email_html(
    new_shows: List[TicketAlert],
    restocked: List[TicketAlert],
    max_price: float,
    index_md_path: str,
) -> str:
    """build an HTML email with alert highlights + full dashboard from index.md."""
    parts = []
    parts.append("""<html><body style="font-family:-apple-system,Arial,sans-serif;color:#222;max-width:700px;margin:0 auto;">""")

    # alert section
    if new_shows:
        parts.append(f"<h2 style='color:#2da44e;'>🆕 {len(new_shows)} new show(s)</h2>")
        parts.append(_alert_table_html(new_shows))

    if restocked:
        parts.append(f"<h2 style='color:#d29922;'>🔄 {len(restocked)} restocked</h2>")
        parts.append(_alert_table_html(restocked))

    # divider
    parts.append("<hr style='border:none;border-top:2px solid #ddd;margin:24px 0;'>")

    # full dashboard from index.md
    dashboard_html = _markdown_to_html(index_md_path)
    if dashboard_html:
        parts.append("<h2>📋 Full Dashboard</h2>")
        parts.append(dashboard_html)

    parts.append("</body></html>")
    return "\n".join(parts)


def _alert_table_html(alerts: List[TicketAlert]) -> str:
    rows = []
    for a in alerts:
        in_stock = [p for p in a.plans if p["available"]]
        prices = sorted(set(p["price"] for p in in_stock))
        price_str = " / ".join(f"¥{p:.0f}" for p in prices)
        rows.append(
            f"<tr><td>{a.category}</td><td><b>{a.show_name}</b></td>"
            f"<td>{a.show_date}</td><td style='color:#d29922;'>{price_str}</td>"
            f"<td>{len(in_stock)} in stock</td></tr>"
        )
    return (
        "<table style='border-collapse:collapse;width:100%;font-size:14px;'>"
        "<tr style='background:#f6f8fa;'><th style='padding:6px 8px;text-align:left;'>Category</th>"
        "<th style='padding:6px 8px;text-align:left;'>Name</th>"
        "<th style='padding:6px 8px;text-align:left;'>Date</th>"
        "<th style='padding:6px 8px;text-align:left;'>Price</th>"
        "<th style='padding:6px 8px;text-align:left;'>Stock</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _markdown_to_html(md_path: str) -> str:
    """convert index.md tables to simple HTML (no external deps)."""
    path = Path(md_path)
    if not path.exists():
        return ""

    lines = path.read_text(encoding="utf-8").splitlines()
    html_parts = []
    in_table = False
    is_header_row = True

    for line in lines:
        stripped = line.strip()

        # skip markdown headings -> convert to HTML headings
        if stripped.startswith("# "):
            html_parts.append(f"<h2>{stripped[2:]}</h2>")
            continue
        if stripped.startswith("## "):
            html_parts.append(f"<h3>{stripped[3:]}</h3>")
            continue
        if stripped.startswith("### "):
            html_parts.append(f"<h4>{stripped[4:]}</h4>")
            continue
        if stripped.startswith("> "):
            html_parts.append(f"<p style='color:#666;font-style:italic;'>{stripped[2:]}</p>")
            continue
        if stripped.startswith("*") and stripped.endswith("*") and len(stripped) > 2:
            html_parts.append(f"<p style='color:#888;'><em>{stripped.strip('*')}</em></p>")
            continue
        if stripped.startswith("<details>") or stripped.startswith("</details>"):
            continue
        if stripped.startswith("<summary>"):
            label = stripped.replace("<summary>", "").replace("</summary>", "")
            html_parts.append(f"<h4>{label}</h4>")
            continue

        # table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]

            # skip separator rows like |---|---|
            if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                is_header_row = False
                continue

            if not in_table:
                html_parts.append(
                    "<table style='border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;'>"
                )
                in_table = True

            tag = "th" if is_header_row else "td"
            style = "padding:5px 8px;border-bottom:1px solid #ddd;text-align:left;"
            if is_header_row:
                style += "background:#f6f8fa;font-weight:600;"
            row = "".join(f"<{tag} style='{style}'>{c}</{tag}>" for c in cells)
            html_parts.append(f"<tr>{row}</tr>")

            if is_header_row:
                is_header_row = False
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False
                is_header_row = True
            if stripped:
                html_parts.append(f"<p>{stripped}</p>")

    if in_table:
        html_parts.append("</table>")

    return "\n".join(html_parts)


# ------------------------------------------------------------------ #
# history persistence
# ------------------------------------------------------------------ #

def _load_history() -> Dict[str, Any]:
    path = Path(HISTORY_FILE)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history(history: Dict[str, Any]):
    path = Path(HISTORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
