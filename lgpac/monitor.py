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

HISTORY_FILE = "data/monitor_history.json"


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

        in_stock = [p for p in affordable_plans if not p["is_stop_sale"]]

        prev = history.get(show.show_id)

        if not prev:
            status = "new"
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
    """collect all seat plans under the price threshold."""
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
            plans_in_stock = [p for p in a.plans if not p["is_stop_sale"]]
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
        in_stock_plans = [p for p in a.plans if not p["is_stop_sale"]]
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
    send alert via webhook.
    supports generic JSON POST — works with bark, telegram bot, slack, dingtalk, etc.
    set LGPAC_WEBHOOK_URL env var or pass directly.
    """
    url = webhook_url or os.environ.get("LGPAC_WEBHOOK_URL", "")
    if not url:
        return

    payload = json.dumps({"text": text, "content": text}, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urlopen(req, timeout=10) as resp:
            logger.info(f"webhook sent: {resp.status}")
    except URLError as e:
        logger.warning(f"webhook failed: {e}")


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
