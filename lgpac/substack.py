"""
substack newsletter monitor via standard RSS feeds.
reads tracked publications from archs_substack/tracked.yml.
fetches https://{slug}.substack.com/feed (standard RSS/Atom XML).
no auth, no scraping, no rate limit concerns.
"""
import logging
import time
import random
import yaml
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from email.utils import parsedate_to_datetime

from lgpac.archive import JsonArchive
from lgpac.notify import send_email, build_html_email

logger = logging.getLogger("lgpac.substack")

FEED_URL_TEMPLATE = "https://{slug}.substack.com/feed"
TRACKED_FILE = "archs_substack/tracked.yml"
ARCHIVE_FILE = "archs_substack/archive.json"
RECENT_HOURS = 24

_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
]


def _get_ua() -> str:
    return random.choice(_UA_LIST)

REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.8


# ------------------------------------------------------------------ #
# tracked publication management (YAML)
# ------------------------------------------------------------------ #

def load_tracked() -> List[Dict[str, Any]]:
    path = Path(TRACKED_FILE)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        return []
    return data


def get_slugs() -> List[str]:
    return [e["slug"] for e in load_tracked() if e.get("slug")]


# ------------------------------------------------------------------ #
# fetch RSS feed
# ------------------------------------------------------------------ #

def fetch_feed(slug: str, feed_url: str = "") -> tuple:
    """
    fetch articles for a tracked publication.
    fallback chain:
      1. substack-api library (uses /api/v1/archive, richest data)
      2. direct RSS fetch (works locally, blocked on Actions by Cloudflare)
      3. substack JSON API /api/v1/posts (same domain, also likely blocked)
      4. rss2json.com proxy (bypasses Cloudflare via third-party server)
    if feed_url is set, skip step 1 and go straight to RSS (non-substack feed).
    returns (status, articles_list).
    status: "ok", "empty", "not_found", "error"
    """
    import requests

    is_substack_default = not feed_url

    if is_substack_default:
        status, articles = _fetch_via_substack_api(slug)
        if status == "ok":
            return status, articles
        if status == "empty":
            return status, articles

    url = feed_url if feed_url else FEED_URL_TEMPLATE.format(slug=slug)

    headers = {
        "User-Agent": _get_ua(),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    blocked_by_cloudflare = False

    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)

        logger.debug(
            f"[@{slug}] HTTP {resp.status_code} | "
            f"content-type: {resp.headers.get('content-type', '?')[:50]} | "
            f"body: {len(resp.text)} bytes | "
            f"final-url: {resp.url[:80]}"
        )

        if resp.status_code == 200:
            has_xml = (
                "<rss" in resp.text[:500]
                or "<feed" in resp.text[:500]
                or "<?xml" in resp.text[:200]
            )
            if has_xml:
                status, articles = _parse_rss(resp.text, slug)
                if status in ("ok", "empty"):
                    return status, articles
                logger.debug(f"[@{slug}] XML parse failed, trying fallbacks")
            else:
                logger.info(f"[@{slug}] 200 but not RSS/XML (first 80 chars: {resp.text[:80]!r})")
            if is_substack_default:
                return _fetch_via_api(slug)
            return "error", []

        if resp.status_code == 404:
            if is_substack_default:
                return _fetch_via_api(slug)
            logger.info(f"[@{slug}] custom feed_url 404: {url[:80]}")
            return "not_found", []

        if resp.status_code in (301, 302):
            location = resp.headers.get("location", "?")
            logger.info(f"[@{slug}] redirect {resp.status_code} -> {location[:80]}")
            return "error", []

        if resp.status_code == 403:
            logger.debug(f"[@{slug}] 403 — Cloudflare/IP block detected")
            blocked_by_cloudflare = True

        elif resp.status_code >= 500:
            logger.info(f"[@{slug}] server error {resp.status_code}")
            return "error", []
        else:
            logger.info(f"[@{slug}] unexpected status {resp.status_code}")
            return "error", []

    except requests.ConnectionError as e:
        logger.info(f"[@{slug}] connection error: {e}")
        blocked_by_cloudflare = True
    except requests.Timeout:
        logger.info(f"[@{slug}] timeout for {url[:60]}")
        return "error", []
    except Exception as e:
        logger.info(f"[@{slug}] error: {type(e).__name__}: {e}")
        blocked_by_cloudflare = True

    if blocked_by_cloudflare:
        if is_substack_default:
            api_status, api_articles = _fetch_via_api(slug)
            if api_status == "ok":
                return api_status, api_articles
        return _fetch_via_rss_proxy(url, slug)

    return "error", []


def _fetch_via_substack_api(slug: str) -> tuple:
    """
    primary method: use substack-api library (pip install substack-api).
    calls /api/v1/archive endpoint which returns rich post metadata.
    """
    try:
        from substack_api import newsletter
    except ImportError:
        logger.debug("substack-api not installed, skipping")
        return "error", []

    try:
        posts = newsletter.get_newsletter_post_metadata(
            slug, start_offset=0, end_offset=12,
        )
    except Exception as e:
        logger.debug(f"[@{slug}] substack-api error: {type(e).__name__}: {e}")
        return "error", []

    if not isinstance(posts, list):
        return "error", []

    articles = []
    for post in posts:
        title = post.get("title", "")
        url = post.get("canonical_url", "")
        pub_date_raw = post.get("post_date", "")
        description = post.get("description", "") or post.get("subtitle", "")

        article_id = url or title
        if not article_id:
            continue

        pub_date = pub_date_raw[:16].replace("T", " ") if pub_date_raw else ""

        articles.append({
            "id": article_id,
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "pub_date_raw": pub_date_raw,
            "description": (description or "")[:300],
            "slug": slug,
            "_source": "substack_api",
        })

    if articles:
        logger.info(f"[@{slug}] substack-api OK: {len(articles)} posts")
        return "ok", articles

    return "empty", []


def _fetch_via_api(slug: str) -> tuple:
    """fallback #2: use Substack's public JSON API (same domain, also may be blocked)."""
    import requests

    api_url = f"https://{slug}.substack.com/api/v1/posts?limit=10"
    try:
        resp = requests.get(api_url, headers={
            "User-Agent": _get_ua(),
            "Accept": "application/json",
        }, timeout=15)

        logger.debug(f"[@{slug}] API fallback: HTTP {resp.status_code}")

        if resp.status_code == 403:
            logger.debug(f"[@{slug}] API also blocked (403)")
            return "blocked", []
        if resp.status_code != 200:
            return "not_found", []

        data = resp.json()
        if not isinstance(data, list):
            return "empty", []
    except Exception as e:
        logger.debug(f"[@{slug}] API fallback error: {e}")
        return "error", []

    articles = []
    for post in data:
        title = post.get("title", "")
        url = post.get("canonical_url", "")
        pub_date_raw = post.get("post_date", "")
        description = post.get("subtitle", "") or post.get("description", "")

        article_id = url or title
        if not article_id:
            continue

        pub_date = pub_date_raw[:16] if pub_date_raw else ""

        articles.append({
            "id": article_id,
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "pub_date_raw": pub_date_raw,
            "description": (description or "")[:300],
            "slug": slug,
            "_source": "api",
        })

    if articles:
        logger.info(f"[@{slug}] API fallback OK: {len(articles)} articles")
        return "ok", articles

    return "empty", []


def _fetch_via_rss_proxy(feed_url: str, slug: str) -> tuple:
    """
    fallback #2: use rss2json.com to proxy-fetch the RSS feed.
    this bypasses Cloudflare IP blocks because rss2json fetches from its own servers.
    free tier: 10,000 requests/day (more than enough for ~40 feeds).
    """
    import requests
    from urllib.parse import quote

    proxy_api = f"https://api.rss2json.com/v1/api.json?rss_url={quote(feed_url, safe='')}"
    try:
        resp = requests.get(proxy_api, timeout=30)
        logger.debug(f"[@{slug}] rss_proxy: HTTP {resp.status_code}")

        if resp.status_code != 200:
            logger.info(f"[@{slug}] rss_proxy failed: HTTP {resp.status_code}")
            return "not_found", []

        data = resp.json()
        if data.get("status") != "ok":
            logger.info(f"[@{slug}] rss_proxy status: {data.get('status', '?')} — {data.get('message', '')[:60]}")
            return "not_found", []
    except Exception as e:
        logger.info(f"[@{slug}] rss_proxy error: {type(e).__name__}: {e}")
        return "error", []

    articles = []
    for item in data.get("items", []):
        title = item.get("title", "")
        link = item.get("link", "")
        pub_date_raw = item.get("pubDate", "")
        description = item.get("description", "")

        article_id = link or title
        if not article_id:
            continue

        pub_date = _parse_proxy_date(pub_date_raw)

        articles.append({
            "id": article_id,
            "title": title,
            "url": link,
            "pub_date": pub_date,
            "pub_date_raw": pub_date_raw,
            "description": (description or "")[:300],
            "slug": slug,
            "_source": "rss_proxy",
        })

    if articles:
        logger.info(f"[@{slug}] rss_proxy OK: {len(articles)} articles")
        return "ok", articles

    return "empty", []


def _parse_proxy_date(raw: str) -> str:
    """parse date from rss2json (format: '2026-03-12 21:16:29')."""
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:16]


def _parse_rss(xml_text: str, slug: str) -> tuple:
    """parse RSS XML into article dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.debug(f"[@{slug}] XML parse error")
        return "error", []

    articles = []

    # RSS 2.0 format
    for item in root.findall(".//item"):
        title = _text(item, "title")
        link = _text(item, "link")
        pub_date_raw = _text(item, "pubDate")
        description = _text(item, "description")

        pub_date = _parse_rss_date(pub_date_raw)
        article_id = link or title

        if not article_id:
            continue

        articles.append({
            "id": article_id,
            "title": title,
            "url": link,
            "pub_date": pub_date,
            "pub_date_raw": pub_date_raw,
            "description": (description or "")[:300],
            "slug": slug,
        })

    if articles:
        return "ok", articles
    return "empty", []


def _text(el, tag: str) -> str:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _parse_rss_date(raw: str) -> str:
    """parse RFC 2822 date from RSS to ISO format."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:16]


# ------------------------------------------------------------------ #
# fetch all publications
# ------------------------------------------------------------------ #

def fetch_all(recent_hours: int = RECENT_HOURS, stage_filter: set = None) -> tuple:
    """
    fetch articles from all tracked substacks.
    filters to articles within recent_hours.
    returns (results_dict, warnings_list).
    """
    tracked = load_tracked()
    if not tracked:
        return {}, []

    if stage_filter is not None:
        before = len(tracked)
        tracked = [e for e in tracked if e.get("wave_stage", -1) in stage_filter]
        logger.info(f"stage filter {stage_filter}: {len(tracked)}/{before} selected")

    results = {}
    warnings = []
    total = len(tracked)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M UTC")

    logger.info(f"fetching {total} substacks, cutoff={recent_hours}h ({cutoff_str})")

    success_count = 0
    empty_count = 0
    error_count = 0
    start_time = time.time()

    for i, entry in enumerate(tracked):
        slug = entry.get("slug", "")
        if not slug:
            continue

        stage = entry.get("wave_stage", "?")
        custom_feed = entry.get("feed_url", "")
        status, articles = fetch_feed(slug, feed_url=custom_feed)

        src_label = "custom" if custom_feed else "substack"
        if status == "ok":
            recent = [a for a in articles if _is_recent(a, cutoff)]
            if recent:
                results[slug] = recent
                success_count += 1
                source = articles[0].get("_source", "rss") if articles else "rss"
                logger.info(f"  [{i+1}/{total}] ✅ {slug} (s{stage}): {len(recent)} recent [{source}/{src_label}]")
            else:
                empty_count += 1
                logger.info(f"  [{i+1}/{total}] ⏭  {slug} (s{stage}): {len(articles)} articles, 0 in window")
        elif status == "empty":
            empty_count += 1
            logger.info(f"  [{i+1}/{total}] 📭 {slug} (s{stage}): feed exists but 0 articles")
        elif status == "not_found":
            error_count += 1
            hint = "slug may be wrong or not on substack"
            if custom_feed:
                hint = f"custom feed_url 404: {custom_feed[:60]}"
            logger.info(f"  [{i+1}/{total}] 🚫 {slug} (s{stage}): 404 — {hint}")
            used_url = custom_feed if custom_feed else FEED_URL_TEMPLATE.format(slug=slug)
            warnings.append({"slug": slug, "name": entry.get("name", slug),
                             "category": entry.get("category", ""),
                             "issue": "feed_not_found", "http_code": 404,
                             "meaning": hint,
                             "feed_url": used_url})
        else:
            error_count += 1
            logger.info(f"  [{i+1}/{total}] ❌ {slug} (s{stage}): fetch error [{src_label}]")
            used_url = custom_feed if custom_feed else FEED_URL_TEMPLATE.format(slug=slug)
            warnings.append({"slug": slug, "name": entry.get("name", slug),
                             "category": entry.get("category", ""),
                             "issue": "fetch_error", "http_code": "N/A",
                             "meaning": "network error, timeout, or server error",
                             "feed_url": used_url})

        if (i + 1) % 10 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / rate if rate > 0 else 0
            pct = (i + 1) * 100 // total
            logger.info(f"  --- {pct}% ({i+1}/{total}) ✅{success_count} ⏭{empty_count} ❌{error_count} | {elapsed:.0f}s elapsed ---")

        if i < total - 1:
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

    elapsed_total = time.time() - start_time
    logger.info(f"fetch complete: {total} feeds in {elapsed_total:.0f}s "
                f"({success_count} active, {empty_count} no-recent, {error_count} errors)")
    return results, warnings


def _is_recent(article: Dict, cutoff: datetime) -> bool:
    raw = article.get("pub_date_raw", "")
    if not raw:
        return False
    try:
        # try RFC 2822 first (RSS format)
        dt = parsedate_to_datetime(raw)
        return dt >= cutoff
    except Exception:
        pass
    try:
        # rss2json format: "2026-03-12 21:16:29"
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        pass
    try:
        # try ISO 8601 (API format: 2026-03-08T12:00:00.000Z)
        clean = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return False


# ------------------------------------------------------------------ #
# archive
# ------------------------------------------------------------------ #

def check_and_archive(all_articles: Dict[str, List[Dict]]) -> List[Dict]:
    archive = JsonArchive(ARCHIVE_FILE, key_field="article_ids")
    archive.load()
    known_ids = set(archive.get("article_ids", []))
    now = datetime.now(timezone.utc).isoformat()

    new_articles = []
    for slug, articles in all_articles.items():
        for article in articles:
            aid = article["id"]
            if aid and aid not in known_ids:
                new_articles.append(article)
                known_ids.add(aid)

    if new_articles:
        archive.set("article_ids", list(known_ids))
        archive.set("last_updated", now)
        archive.set("total_count", len(known_ids))
        archive.save()
        logger.info(f"archived {len(new_articles)} new articles (total: {len(known_ids)})")
    else:
        logger.info("no new articles")

    return new_articles


# ------------------------------------------------------------------ #
# page generation (grouped by wave_stage)
# ------------------------------------------------------------------ #

STAGE_META = {
    0: ("🔬 Stage 0: Frontier Research", "primary sources, arXiv-level analysis"),
    1: ("📡 Stage 1: Expert Interpretation", "practitioner breakdowns, early implications"),
    2: ("🚀 Stage 2: Industry Impact", "business analysis, wide adoption discussion"),
    3: ("📺 Stage 3: Mainstream Digest", "popularized summaries, mass reach"),
    4: ("💀 Stage 4: Hustle/Fading", "repackaged content, low signal"),
}


def generate_page(all_articles: Dict[str, List[Dict]], warnings: List[Dict], output_path: str = "docs_substack/index.md"):
    tracked = load_tracked()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    active = len(all_articles)
    total = len(tracked)
    entry_map = {e["slug"]: e for e in tracked}

    lines = [
        "# 📰 Substack Newsletter Monitor",
        "",
        f"> updated: {now} · {active}/{total} feeds active",
        "> grouped by **wave_stage**",
        "",
    ]

    stage_groups = {s: [] for s in range(5)}
    for slug, articles in all_articles.items():
        if not articles:
            continue
        entry = entry_map.get(slug, {})
        stage = entry.get("wave_stage", 2)
        stage_groups[stage].append((slug, entry, articles))

    for stage in range(5):
        title, desc = STAGE_META[stage]
        items = stage_groups.get(stage, [])

        lines.append(f"## {title}")
        lines.append(f"> {desc}")
        lines.append("")

        if not items:
            lines.append("*no recent articles*")
            lines.append("")
            continue

        lines.append("| Publication | Latest Article | Date |")
        lines.append("|------------|---------------|------|")

        for slug, entry, articles in items:
            a = articles[0]
            name = entry.get("name", slug)
            title_text = (a.get("title", "") or "")[:80].replace("|", "∣")
            url = a.get("url", "")
            date = a.get("pub_date", "")[:10]
            lines.append(f"| {name} | [{title_text}]({url}) | {date} |")

        lines.append("")

    if warnings:
        lines.append(f"<details><summary>⚠️ warnings ({len(warnings)} feeds)</summary>")
        lines.append("")
        lines.append("| Slug | Name | Issue |")
        lines.append("|------|------|-------|")
        for w in warnings:
            lines.append(f"| {w['slug']} | {w['name']} | {w['issue']} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"substack page generated: {path}")


# ------------------------------------------------------------------ #
# email
# ------------------------------------------------------------------ #

def send_email_alert(new_articles: List[Dict], warnings: List[Dict] = None) -> bool:
    if not new_articles and not warnings:
        return False

    tracked = load_tracked()
    entry_map = {e["slug"]: e for e in tracked}

    stage_articles = {s: [] for s in range(5)}
    for a in new_articles:
        slug = a.get("slug", "")
        entry = entry_map.get(slug, {})
        stage = entry.get("wave_stage", 2)
        stage_articles[stage].append(a)

    stage_colors = {0: "#6e40c9", 1: "#1f6feb", 2: "#d29922", 3: "#da3633", 4: "#8b949e"}

    parts = ['<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:0 auto;">']
    parts.append(f'<h2 style="color:#ff6719;">📰 {len(new_articles)} new article(s)</h2>')

    for stage in range(5):
        articles = stage_articles.get(stage, [])
        if not articles:
            continue
        title, desc = STAGE_META[stage]
        color = stage_colors[stage]
        parts.append(f'<h3 style="color:{color};margin-top:20px;">{title}</h3>')
        parts.append('<table style="border-collapse:collapse;width:100%;font-size:14px;">')
        parts.append(
            '<tr style="background:#f6f8fa;">'
            '<th style="padding:6px 8px;text-align:left;">Publication</th>'
            '<th style="padding:6px 8px;text-align:left;">Article</th>'
            '<th style="padding:6px 8px;text-align:left;">Date</th></tr>'
        )
        for a in articles:
            slug = a.get("slug", "")
            name = entry_map.get(slug, {}).get("name", slug)
            title_text = (a.get("title", "") or "")[:120]
            url = a.get("url", "#")
            date = a.get("pub_date", "")[:10]
            parts.append(
                f'<tr><td style="padding:6px 8px;color:#ff6719;">{name}</td>'
                f'<td style="padding:6px 8px;"><a href="{url}" style="color:#1a73e8;text-decoration:none;">{title_text}</a></td>'
                f'<td style="padding:6px 8px;color:#888;">{date}</td></tr>'
            )
        parts.append('</table>')

    if warnings:
        # HTTP status code reference
        code_ref = {
            200: "OK (but content not RSS)",
            301: "moved permanently (slug changed)",
            302: "temporary redirect",
            404: "not found (slug may be wrong or deleted)",
            429: "rate limited (too many requests)",
            500: "server error",
            "N/A": "network error / timeout / connection refused",
        }

        parts.append('<hr style="border:none;border-top:1px solid #ddd;margin:24px 0;">')
        parts.append(f'<h3 style="color:#da3633;">⚠ Feed Status Report ({len(warnings)} issues)</h3>')

        # summary by issue type
        from collections import Counter
        issue_counts = Counter(w.get("issue", "unknown") for w in warnings)
        parts.append('<p style="font-size:13px;color:#666;">')
        issue_icons = {"feed_not_found": "🚫", "fetch_error": "❌", "not_rss": "⚠️"}
        for issue, count in issue_counts.most_common():
            icon = issue_icons.get(issue, "❓")
            parts.append(f'{icon} <b>{count}</b> × {issue}<br>')
        parts.append('</p>')

        # detailed table
        parts.append('<table style="border-collapse:collapse;width:100%;font-size:12px;">')
        parts.append(
            '<tr style="background:#f6f8fa;">'
            '<th style="padding:5px 8px;text-align:left;">Feed</th>'
            '<th style="padding:5px 8px;text-align:left;">HTTP</th>'
            '<th style="padding:5px 8px;text-align:left;">Meaning</th>'
            '<th style="padding:5px 8px;text-align:left;">Action</th></tr>'
        )
        for w in warnings:
            slug_name = w.get("slug", "?")
            http_code = w.get("http_code", "?")
            meaning = w.get("meaning", code_ref.get(http_code, "unknown"))
            action = "check slug or add feed_url in tracked.yml" if http_code == 404 else "retry later"
            feed_url = w.get("feed_url", f"https://{slug_name}.substack.com/feed")
            parts.append(
                f'<tr>'
                f'<td style="padding:4px 8px;"><a href="{feed_url}" style="color:#1a73e8;">{slug_name}</a></td>'
                f'<td style="padding:4px 8px;color:#da3633;font-weight:600;">{http_code}</td>'
                f'<td style="padding:4px 8px;color:#666;">{meaning}</td>'
                f'<td style="padding:4px 8px;color:#888;">{action}</td>'
                f'</tr>'
            )
        parts.append('</table>')

    parts.append('</body></html>')
    html = "\n".join(parts)

    subject_extra = ""
    if warnings:
        subject_extra = f", {len(warnings)} issues"
    return send_email(f"[substack] {len(new_articles)} new article(s){subject_extra}", html)


# ------------------------------------------------------------------ #
# run
# ------------------------------------------------------------------ #

def run_monitor(notify: bool = False, page: bool = False, recent_hours: int = RECENT_HOURS, stage_filter: set = None) -> tuple:
    logger.info(f"=== substack run_monitor start (hours={recent_hours}, notify={notify}, page={page}) ===")

    all_articles, warnings = fetch_all(recent_hours=recent_hours, stage_filter=stage_filter)
    new_articles = check_and_archive(all_articles)

    if page:
        generate_page(all_articles, warnings)

    if notify and (new_articles or warnings):
        send_email_alert(new_articles, warnings)

    logger.info(f"=== substack run_monitor complete ({len(new_articles)} new) ===")
    return new_articles, warnings
