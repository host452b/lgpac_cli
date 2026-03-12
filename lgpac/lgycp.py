"""
weixin article monitor with fallback search providers.
primary: sogou weixin search
fallback: baidu / google site:mp.weixin.qq.com / bing

all providers return the same normalized format:
  [{"title": "...", "url": "...", "source": "..."}]
"""
import re
import json
import logging
import os
import html as html_mod
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger("lgpac.lgycp")

ARCHIVE_FILE = "archs_lgycp/archive.json"

KEYWORDS = [
    "报名", "通知", "开课", "招生", "招募", "补报", "托管",
    "春季", "夏季", "秋季", "冬季",
    "儿童剧", "英语", "体能", "健身", "舞蹈",
    "儿童活动", "节目", "演出", "选拔", "体验",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------------ #
# provider: sogou weixin (primary)
# ------------------------------------------------------------------ #

def _fetch_sogou(query: str) -> List[Dict[str, str]]:
    import requests

    url = (
        "https://weixin.sogou.com/weixin?"
        f"type=2&s_from=input&ie=utf8&query={query}"
    )
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()

    title_pattern = re.compile(
        r'<a\s+target="_blank"\s+href="([^"]+)"\s+'
        r'id="sogou_vr_\d+_title_(\d+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    source_pattern = re.compile(
        r'id="sogou_vr_\d+_title_(\d+)".*?'
        r'class="s-p"[^>]*>.*?<a[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    results = []
    for m in title_pattern.finditer(resp.text):
        link, idx, raw = m.group(1), m.group(2), m.group(3)
        title = _clean_html(raw)
        if not title:
            continue
        if link.startswith("/link?"):
            link = "https://weixin.sogou.com" + link
        results.append({"title": title, "url": html_mod.unescape(link), "index": idx, "source": ""})

    for m in source_pattern.finditer(resp.text):
        src = _clean_html(m.group(2))
        for r in results:
            if r["index"] == m.group(1):
                r["source"] = src
                break

    for r in results:
        r.pop("index", None)

    return results


# ------------------------------------------------------------------ #
# provider: baidu (fallback 1)
# ------------------------------------------------------------------ #

def _fetch_baidu(query: str) -> List[Dict[str, str]]:
    import requests

    url = f"https://www.baidu.com/s?wd={query}+site%3Amp.weixin.qq.com&rn=20"
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=15,
    )
    resp.raise_for_status()

    # baidu result titles: <h3 class="c-title ..."><a href="...">TITLE</a></h3>
    pattern = re.compile(
        r'<h3[^>]*class="[^"]*c-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    results = []
    for m in pattern.finditer(resp.text):
        link = html_mod.unescape(m.group(1))
        title = _clean_html(m.group(2))
        if title and query.split()[0] in title:
            results.append({"title": title, "url": link, "source": ""})

    return results


# ------------------------------------------------------------------ #
# provider: bing (fallback 2)
# ------------------------------------------------------------------ #

def _fetch_bing(query: str) -> List[Dict[str, str]]:
    import requests

    url = f"https://www.bing.com/search?q={query}+site%3Amp.weixin.qq.com&count=20"
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=15,
    )
    resp.raise_for_status()

    # bing: <h2><a href="...">TITLE</a></h2>
    pattern = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL)

    results = []
    for m in pattern.finditer(resp.text):
        link = html_mod.unescape(m.group(1))
        title = _clean_html(m.group(2))
        if title and query.split()[0] in title:
            results.append({"title": title, "url": link, "source": ""})

    return results


# ------------------------------------------------------------------ #
# fetch with fallback chain
# ------------------------------------------------------------------ #

PROVIDERS: List[tuple] = [
    ("sogou", _fetch_sogou),
    ("baidu", _fetch_baidu),
    ("bing", _fetch_bing),
]


def fetch_articles(query: str = "临港少年宫") -> List[Dict[str, str]]:
    """try each provider in order until one returns results."""
    for name, fetcher in PROVIDERS:
        try:
            articles = fetcher(query)
            if articles:
                logger.info(f"[{name}] fetched {len(articles)} articles")
                return articles
            logger.warning(f"[{name}] returned 0 results, trying fallback")
        except Exception as e:
            logger.warning(f"[{name}] failed: {type(e).__name__} - {e}, trying fallback")

    logger.error("all providers failed, no articles fetched")
    return []


# ------------------------------------------------------------------ #
# filter / archive / email / run (unchanged interface)
# ------------------------------------------------------------------ #

def filter_relevant(articles: List[Dict], year_range: tuple = (2026, 2056)) -> List[Dict]:
    """filter articles matching keywords and year range."""
    matched = []
    for article in articles:
        title = article.get("title", "")
        if not any(kw in title for kw in KEYWORDS):
            continue
        year_match = re.search(r'20[2-5]\d', title)
        if year_match:
            year = int(year_match.group())
            if year < year_range[0] or year > year_range[1]:
                continue
        matched.append(article)
    logger.info(f"filtered {len(matched)}/{len(articles)} matching keywords")
    return matched


def check_and_archive(articles: List[Dict]) -> List[Dict]:
    """compare against archive, return only NEW articles, update archive."""
    archive = _load_archive()
    known_titles = set(archive.get("titles", {}).keys())
    now = datetime.now(timezone.utc).isoformat()

    new_articles = []
    for article in articles:
        title = article["title"]
        if title not in known_titles:
            new_articles.append(article)
            archive.setdefault("titles", {})[title] = {
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "found_at": now,
            }

    if new_articles:
        archive["last_updated"] = now
        archive["total_count"] = len(archive.get("titles", {}))
        _save_archive(archive)
        logger.info(f"archived {len(new_articles)} new (total: {archive['total_count']})")
    else:
        logger.info("no new articles")

    return new_articles


def send_email(new_articles: List[Dict]) -> bool:
    """send email notification for new articles."""
    import smtplib
    from email.mime.text import MIMEText

    to_addr = os.environ.get("LGPAC_NOTIFY_EMAIL", "").strip()
    smtp_user = os.environ.get("LGPAC_SMTP_USER", "").strip()
    smtp_pass = os.environ.get("LGPAC_SMTP_PASS", "").strip()
    smtp_server = os.environ.get("LGPAC_SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("LGPAC_SMTP_PORT", "465"))

    if not to_addr or not smtp_user or not smtp_pass:
        logger.debug("lgycp email: credentials not set, skipped")
        return False

    if not new_articles:
        return False

    rows = []
    for a in new_articles:
        title = a["title"]
        url = a.get("url", "#")
        source = a.get("source", "")
        rows.append(
            f'<tr><td style="padding:6px 8px;">'
            f'<a href="{url}" style="color:#1a73e8;text-decoration:none;">{title}</a>'
            f'</td><td style="padding:6px 8px;color:#666;">{source}</td></tr>'
        )

    body = (
        '<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:600px;margin:0 auto;">'
        f'<h2 style="color:#2da44e;">📢 {len(new_articles)} new article(s)</h2>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        '<tr style="background:#f6f8fa;"><th style="padding:6px 8px;text-align:left;">Title</th>'
        '<th style="padding:6px 8px;text-align:left;">Source</th></tr>'
        + "".join(rows)
        + '</table></body></html>'
    )

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"[lgycp] {len(new_articles)} new article(s)"
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_addr], msg.as_string())
        logger.info("lgycp email: sent successfully")
        return True
    except Exception as e:
        logger.warning(f"lgycp email: send failed - {type(e).__name__}")
        return False


def generate_page(output_path: str = "docs_lgycp/index.md"):
    """generate a markdown page listing all archived articles."""
    archive = _load_archive()
    titles = archive.get("titles", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("# 📢 Article Monitor")
    lines.append("")
    lines.append(f"> updated: {now} · {len(titles)} articles archived")
    lines.append("")

    if not titles:
        lines.append("*waiting for first run...*")
    else:
        lines.append("| # | Title | Source | Found |")
        lines.append("|---|-------|--------|-------|")

        sorted_items = sorted(titles.items(), key=lambda x: x[1].get("found_at", ""), reverse=True)
        for i, (title, info) in enumerate(sorted_items, 1):
            url = info.get("url", "")
            source = info.get("source", "")
            found = info.get("found_at", "")[:10]
            title_esc = title.replace("|", "∣")
            if url:
                title_cell = f"[{title_esc}]({url})"
            else:
                title_cell = title_esc
            lines.append(f"| {i} | {title_cell} | {source} | {found} |")

    lines.append("")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"lgycp page generated: {path}")


def run_monitor(query: str = "临港少年宫", notify: bool = False, page: bool = False) -> List[Dict]:
    """full pipeline: fetch (with fallback) -> filter -> archive -> page -> notify."""
    articles = fetch_articles(query)
    relevant = filter_relevant(articles)
    new_articles = check_and_archive(relevant)

    if page:
        generate_page()

    if new_articles and notify:
        send_email(new_articles)

    return new_articles


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #

def _clean_html(raw: str) -> str:
    text = re.sub(r'<[^>]+>', '', raw)
    text = re.sub(r'<!--.*?-->', '', text)
    return html_mod.unescape(text).strip()


def _load_archive() -> Dict[str, Any]:
    path = Path(ARCHIVE_FILE)
    if not path.exists():
        return {"titles": {}, "total_count": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"titles": {}, "total_count": 0}


def _save_archive(archive: Dict[str, Any]):
    path = Path(ARCHIVE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
