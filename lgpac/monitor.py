"""
ticket availability monitor.
filters shows by price threshold, tracks stock changes,
and generates alerts for affordable in-stock tickets.
"""
import logging
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Dict, Any

from lgpac.models import Show
from lgpac.archive import JsonArchive
from lgpac.notify import send_email, send_webhook, build_html_email

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
    status: str = ""


# ------------------------------------------------------------------ #
# analysis
# ------------------------------------------------------------------ #

def analyze_shows(
    shows: List[Show],
    max_price: float = 120.0,
) -> List[TicketAlert]:
    """scan all shows for affordable tickets, compare with history."""
    archive = JsonArchive(HISTORY_FILE, key_field="_raw")
    history = archive.load()
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

        history[show.show_id] = {
            "name": show.name,
            "first_seen": first_seen,
            "last_checked": now,
            "had_stock": len(in_stock) > 0,
            "plan_count": len(affordable_plans),
        }

    archive.set("_raw", None)
    archive._data = history
    archive.save()

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


# ------------------------------------------------------------------ #
# formatting
# ------------------------------------------------------------------ #

def format_alerts_text(alerts: List[TicketAlert], max_price: float) -> str:
    """format alerts as plain text for console or webhook."""
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


# ------------------------------------------------------------------ #
# email (uses shared notify)
# ------------------------------------------------------------------ #

def send_email_alert(alerts: List[TicketAlert], max_price: float, index_md_path: str = "docs_lgpac/index.md") -> bool:
    """send email for NEW or BACK_IN_STOCK shows, with full dashboard attached."""
    worth_emailing = [a for a in alerts if a.status in ("new", "back_in_stock")]
    if not worth_emailing:
        logger.info("email: no new/restocked shows, skipped")
        return False

    new_shows = [a for a in worth_emailing if a.status == "new"]
    restocked = [a for a in worth_emailing if a.status == "back_in_stock"]

    subject_parts = []
    if new_shows:
        subject_parts.append(f"{len(new_shows)} new")
    if restocked:
        subject_parts.append(f"{len(restocked)} restocked")
    subject = f"[lgpac] {' + '.join(subject_parts)} (under ¥{max_price:.0f})"

    html = _build_email_html(new_shows, restocked, max_price, index_md_path)
    return send_email(subject, html)


def _build_email_html(
    new_shows: List[TicketAlert],
    restocked: List[TicketAlert],
    max_price: float,
    index_md_path: str,
) -> str:
    parts = []
    parts.append('<html><body style="font-family:-apple-system,Arial,sans-serif;color:#222;max-width:700px;margin:0 auto;">')

    if new_shows:
        parts.append(f"<h2 style='color:#2da44e;'>🆕 {len(new_shows)} new show(s)</h2>")
        parts.append(_alert_table_html(new_shows))

    if restocked:
        parts.append(f"<h2 style='color:#d29922;'>🔄 {len(restocked)} restocked</h2>")
        parts.append(_alert_table_html(restocked))

    parts.append("<hr style='border:none;border-top:2px solid #ddd;margin:24px 0;'>")

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
        + "".join(rows) + "</table>"
    )


def _markdown_to_html(md_path: str) -> str:
    """convert index.md tables to simple HTML."""
    path = Path(md_path)
    if not path.exists():
        return ""

    lines = path.read_text(encoding="utf-8").splitlines()
    html_parts = []
    in_table = False
    is_header_row = True

    for line in lines:
        stripped = line.strip()
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

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                is_header_row = False
                continue
            if not in_table:
                html_parts.append("<table style='border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;'>")
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
