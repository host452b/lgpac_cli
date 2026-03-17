"""
email generator — multipart HTML + plain text digest.
groups feeds by wave_stage to match the tracked.yml propagation model.
"""
import html
import smtplib
import logging
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

STAGE_META = {
    0: {"icon": "\U0001f52c", "label": "Ignition",  "desc": "frontier research — papers, prototypes, model releases",           "color": "#6e40c9", "bg": "#f5f0ff"},
    1: {"icon": "\U0001f4e1", "label": "Momentum",  "desc": "expert interpretation — practitioners amplifying within their circle", "color": "#1f6feb", "bg": "#ddf4ff"},
    2: {"icon": "\U0001f680", "label": "Explosion",  "desc": "industry impact — business analysis, cross-industry spread",         "color": "#d29922", "bg": "#fff8c5"},
    3: {"icon": "\U0001f4fa", "label": "Decay",      "desc": "mainstream — mass media, generalists, popularization",               "color": "#da3633", "bg": "#ffebe9"},
    4: {"icon": "\U0001f480", "label": "Fading",     "desc": "hustle / outdated — recycled, monetized, reverse indicator",         "color": "#8b949e", "bg": "#f6f8fa"},
}


def _group_by_stage(feed_results):
    """group feed results by wave_stage, sorted by stage number."""
    groups = defaultdict(list)
    for r in feed_results:
        stage = r.get("wave_stage", -1)
        groups[stage].append(r)
    return dict(sorted(groups.items()))


# ------------------------------------------------------------------ #
# plain text
# ------------------------------------------------------------------ #

def generate_plain_text(feed_results, hours):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_posts = sum(len(r["posts"]) for r in feed_results)
    with_posts = [r for r in feed_results if r["posts"]]
    failed = [r for r in feed_results if r["status"] == "error"]

    lines = [
        f"RSS Digest — {date_str} ({hours}h window)",
        f"{total_posts} posts from {len(with_posts)}/{len(feed_results)} feeds",
        "",
    ]

    stage_groups = _group_by_stage(feed_results)
    for stage, items in stage_groups.items():
        meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}", "desc": ""})
        active = [r for r in items if r["posts"]]
        if not active:
            continue

        lines.append(f"{meta['icon']} Stage {stage}: {meta['label']}")
        lines.append(f"   {meta['desc']}")
        lines.append("")

        for r in active:
            cat = r.get("feed_category", "")
            cat_tag = f"[{cat}] " if cat else ""
            lines.append(f"  {cat_tag}{r['name']}")
            for p in r["posts"]:
                lines.append(f"    - {html.unescape(p['title'])}")
                lines.append(f"      {p['link']}")
            lines.append("")

    if failed:
        lines.append(f"--- {len(failed)} feeds failed ---")
        for r in failed:
            lines.append(f"  {r['name']}: {r.get('error', 'unknown')}")

    return "\n".join(lines)


# ------------------------------------------------------------------ #
# HTML
# ------------------------------------------------------------------ #

def generate_html(feed_results, hours):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_posts = sum(len(r["posts"]) for r in feed_results)
    with_posts = [r for r in feed_results if r["posts"]]
    failed = [r for r in feed_results if r["status"] == "error"]

    p = ['<html><head><meta charset="utf-8"><style>']
    p.append("""
body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
       font-size: 15px; line-height: 1.6; color: #1f2328;
       max-width: 680px; margin: 0 auto; padding: 20px; background: #fff; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
.header { text-align: center; padding: 16px 0 12px; border-bottom: 2px solid #d0d7de; margin-bottom: 24px; }
.header h1 { font-size: 22px; margin: 0 0 4px; color: #1f2328; }
.header .subtitle { font-size: 13px; color: #656d76; }
.stage-section { margin-bottom: 28px; }
.stage-header { padding: 10px 16px; border-radius: 8px; margin-bottom: 12px; }
.stage-header h2 { font-size: 17px; margin: 0; }
.stage-header .stage-desc { font-size: 12px; margin-top: 2px; opacity: 0.8; }
.feed-block { margin: 0 0 16px 0; padding-left: 16px; border-left: 3px solid #d0d7de; }
.feed-name { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.feed-name .cat-badge { display: inline-block; font-size: 11px; font-weight: 500;
    padding: 1px 6px; border-radius: 10px; background: #f6f8fa; color: #656d76;
    border: 1px solid #d0d7de; margin-right: 6px; vertical-align: middle; }
.post { margin: 6px 0; }
.post a { font-size: 14px; font-weight: 500; }
.post .excerpt { font-size: 13px; color: #656d76; margin-top: 1px; }
.summary { margin-top: 28px; padding: 16px; background: #f6f8fa; border-radius: 8px;
           font-size: 13px; color: #656d76; }
.summary .stage-row { display: flex; justify-content: space-between; padding: 2px 0; }
.summary .stage-dot { display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.error-section { margin-top: 16px; padding: 12px; background: #ffebe9; border-radius: 6px;
                 font-size: 13px; color: #82071e; }
""")
    p.append('</style></head><body>')

    # header
    p.append('<div class="header">')
    p.append(f'<h1>RSS Digest</h1>')
    p.append(f'<div class="subtitle">{date_str} &middot; {hours}h window &middot; '
             f'{total_posts} posts from {len(with_posts)}/{len(feed_results)} feeds</div>')
    p.append('</div>')

    # stage sections
    stage_groups = _group_by_stage(feed_results)
    stage_stats = {}

    for stage, items in stage_groups.items():
        meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}", "desc": "",
                                       "color": "#656d76", "bg": "#f6f8fa"})
        active = [r for r in items if r["posts"]]
        post_count = sum(len(r["posts"]) for r in active)
        stage_stats[stage] = {"total": len(items), "active": len(active), "posts": post_count}

        if not active:
            continue

        p.append('<div class="stage-section">')
        p.append(f'<div class="stage-header" style="background:{meta["bg"]};color:{meta["color"]};">')
        p.append(f'<h2>{meta["icon"]} Stage {stage}: {meta["label"]}</h2>')
        p.append(f'<div class="stage-desc">{meta["desc"]}</div>')
        p.append('</div>')

        for r in active:
            cat = r.get("feed_category", "")
            site_url = r.get("site_url", "")
            name_escaped = html.escape(r["name"])

            p.append(f'<div class="feed-block" style="border-left-color:{meta["color"]};">')
            p.append('<div class="feed-name">')
            if cat:
                p.append(f'<span class="cat-badge">{html.escape(cat)}</span>')
            if site_url:
                p.append(f'<a href="{html.escape(site_url)}">{name_escaped}</a>')
            else:
                p.append(name_escaped)
            p.append('</div>')

            for post in r["posts"]:
                title = html.escape(html.unescape(post["title"]))
                link = html.escape(post["link"])
                p.append('<div class="post">')
                p.append(f'<a href="{link}">{title}</a>')
                if post.get("excerpt"):
                    excerpt = html.escape(html.unescape(post["excerpt"]))
                    p.append(f'<div class="excerpt">{excerpt}</div>')
                p.append('</div>')

            p.append('</div>')

        p.append('</div>')

    # summary
    p.append('<div class="summary">')
    p.append(f'<div style="font-weight:600;margin-bottom:8px;">Summary</div>')
    for stage in sorted(stage_stats.keys()):
        if stage < 0:
            continue
        meta = STAGE_META.get(stage, {"icon": "?", "label": f"Stage {stage}", "color": "#656d76"})
        s = stage_stats[stage]
        p.append(f'<div class="stage-row">'
                 f'<span><span class="stage-dot" style="background:{meta["color"]};"></span>'
                 f'{meta["icon"]} {meta["label"]}</span>'
                 f'<span>{s["active"]}/{s["total"]} feeds &middot; {s["posts"]} posts</span></div>')

    if failed:
        p.append(f'<div class="error-section">')
        p.append(f'<b>{len(failed)} feeds failed:</b><br>')
        for r in failed:
            name = html.escape(r["name"])
            err = html.escape(r.get("error", "unknown"))
            p.append(f'{name}: {err}<br>')
        p.append('</div>')

    p.append('</div>')
    p.append('</body></html>')
    return "\n".join(p)


# ------------------------------------------------------------------ #
# build + send
# ------------------------------------------------------------------ #

def build_message(feed_results, from_email, to_email, hours=24):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_posts = sum(len(r["posts"]) for r in feed_results)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"RSS Digest — {date_str} ({total_posts} posts)"
    msg["From"] = from_email
    msg["To"] = to_email

    msg.attach(MIMEText(generate_plain_text(feed_results, hours), "plain", "utf-8"))
    msg.attach(MIMEText(generate_html(feed_results, hours), "html", "utf-8"))

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
