"""
Hacker News + zeli.app daily top-10 tracker.

data sources (fallback chain — tries A→B→C→D until one succeeds):

  HN sources:
    A. Firebase API   — hacker-news.firebaseio.com/v0  (official, public, no auth)
    B. Algolia API    — hn.algolia.com/api/v1          (public search API)
    C. hnrss.org RSS  — hnrss.org/best?count=10        (third-party RSS feed)
    D. last archive   — archs_hn/archive.json           (cached from previous run)

  zeli sources:
    A. HTML fetch     — zeli.app/zh/hacker-news         (robots.txt: Allow)
    B. skip           — use HN data if zeli is down

crawler etiquette:
  - respects robots.txt (zeli Allow /*/hacker-news; HN API is separate domain)
  - User-Agent identifies the bot with source repo URL
  - request delay ≥ 0.5s between API calls, ≥ 1s for HTML
  - single fetch for zeli (one page), serial for HN items
  - no authentication bypass, no headless browser
"""
import re
import json
import logging
import time
import random
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from lgpac.archive import JsonArchive
from lgpac.notify import send_email, build_html_email

logger = logging.getLogger("lgpac.hn")

ARCHIVE_FILE = "archs_hn/archive.json"
DOCS_FILE = "docs_hn/index.md"
TOP_N = 10

USER_AGENT = "lgpac-bot/1.0 (+https://github.com/host452b/lgpac_cli)"

# request politeness
API_DELAY = 0.5      # between Firebase/Algolia API calls
HTML_DELAY = 1.0      # before/after HTML fetch
REQUEST_TIMEOUT = 15   # seconds


# ------------------------------------------------------------------ #
# HN source A: Firebase API (official, public)
# ------------------------------------------------------------------ #

def _fetch_hn_firebase(top_n: int = TOP_N) -> Tuple[List[Dict], str]:
    """fetch top stories via HN's official Firebase API."""
    import requests

    logger.info("HN source A: Firebase API")
    base = "https://hacker-news.firebaseio.com/v0"
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(f"{base}/topstories.json", headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    story_ids = resp.json()[:top_n]

    stories = []
    for sid in story_ids:
        time.sleep(API_DELAY)
        r = requests.get(f"{base}/item/{sid}.json", headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        item = r.json()
        if item and item.get("type") == "story":
            stories.append({
                "id": str(item["id"]),
                "title": item.get("title", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={item['id']}"),
                "score": item.get("score", 0),
                "by": item.get("by", ""),
                "time": item.get("time", 0),
                "comments": item.get("descendants", 0),
                "source": "firebase",
            })

    logger.info(f"  fetched {len(stories)} stories via Firebase")
    return stories, "firebase"


# ------------------------------------------------------------------ #
# HN source B: Algolia API (public search)
# ------------------------------------------------------------------ #

def _fetch_hn_algolia(top_n: int = TOP_N) -> Tuple[List[Dict], str]:
    """fetch front page stories via Algolia's HN search API."""
    import requests

    logger.info("HN source B: Algolia API")
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=front_page&hitsPerPage={top_n}"
    )
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    hits = resp.json().get("hits", [])

    stories = []
    for h in hits[:top_n]:
        stories.append({
            "id": str(h.get("objectID", "")),
            "title": h.get("title", ""),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
            "score": h.get("points", 0),
            "by": h.get("author", ""),
            "time": int(datetime.fromisoformat(h["created_at"].replace("Z", "+00:00")).timestamp()) if h.get("created_at") else 0,
            "comments": h.get("num_comments", 0),
            "source": "algolia",
        })

    logger.info(f"  fetched {len(stories)} stories via Algolia")
    return stories, "algolia"


# ------------------------------------------------------------------ #
# HN source C: hnrss.org RSS feed
# ------------------------------------------------------------------ #

def _fetch_hn_rss(top_n: int = TOP_N) -> Tuple[List[Dict], str]:
    """fetch best stories via hnrss.org RSS feed."""
    import requests

    logger.info("HN source C: hnrss.org RSS")
    url = f"https://hnrss.org/best?count={top_n}"
    headers = {"User-Agent": USER_AGENT}

    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    stories = []

    for item in root.findall(".//item")[:top_n]:
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        description = item.findtext("description", "")

        # extract HN item ID from comments URL
        comments_url = item.findtext("comments", "")
        hn_id = ""
        if "id=" in comments_url:
            hn_id = comments_url.split("id=")[-1]

        stories.append({
            "id": hn_id,
            "title": title,
            "url": link or comments_url,
            "score": 0,
            "by": "",
            "time": 0,
            "comments": 0,
            "source": "hnrss",
        })

    logger.info(f"  fetched {len(stories)} stories via hnrss.org")
    return stories, "hnrss"


# ------------------------------------------------------------------ #
# HN source D: last archive (offline fallback)
# ------------------------------------------------------------------ #

def _fetch_hn_archive(archive: JsonArchive) -> Tuple[List[Dict], str]:
    """return the most recent run from archive as fallback."""
    logger.info("HN source D: archive fallback")
    runs = archive.get("runs", [])
    if not runs:
        return [], "archive_empty"

    last = runs[-1]
    stories = last.get("hn_stories", [])
    logger.info(f"  loaded {len(stories)} stories from archive ({last.get('date', '?')})")
    return stories, "archive"


# ------------------------------------------------------------------ #
# HN fetch with fallback chain
# ------------------------------------------------------------------ #

def fetch_hn_top(top_n: int = TOP_N, archive: Optional[JsonArchive] = None) -> Tuple[List[Dict], str]:
    """try sources A→B→C→D, return first success."""
    sources = [
        ("Firebase API", _fetch_hn_firebase),
        ("Algolia API", _fetch_hn_algolia),
        ("hnrss.org RSS", _fetch_hn_rss),
    ]

    for name, fetcher in sources:
        try:
            stories, source = fetcher(top_n)
            if stories:
                return stories, source
            logger.warning(f"  {name}: returned 0 stories, trying next")
        except Exception as e:
            logger.warning(f"  {name} failed: {e}")
            time.sleep(1)

    # source D: archive fallback
    if archive:
        stories, source = _fetch_hn_archive(archive)
        if stories:
            return stories, source

    logger.error("all HN sources failed")
    return [], "all_failed"


# ------------------------------------------------------------------ #
# zeli.app source A: HTML fetch (robots.txt allows /*/hacker-news)
# ------------------------------------------------------------------ #

def _fetch_zeli_html(top_n: int = TOP_N) -> Tuple[List[Dict], str]:
    """fetch zeli.app Chinese HN digest via polite HTML request."""
    import requests

    logger.info("zeli source A: HTML fetch")
    url = "https://zeli.app/zh"
    headers = {"User-Agent": USER_AGENT}

    time.sleep(HTML_DELAY)
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    # extract article cards: title is in <a> tags inside bg-card containers
    # pattern: find title links and their associated scores
    title_pattern = re.compile(
        r'<a[^>]*href="(https://news\.ycombinator\.com/item\?id=\d+)"[^>]*>'
        r'\s*<h2[^>]*>(.*?)</h2>',
        re.DOTALL,
    )

    stories = []
    for match in title_pattern.finditer(html):
        href, title = match.groups()
        title = re.sub(r'<[^>]+>', '', title).strip()
        if title:
            hn_id = href.split("id=")[-1] if "id=" in href else ""
            stories.append({
                "id": hn_id,
                "title_zh": title,
                "hn_url": href,
                "source": "zeli_html",
            })

    # fallback: try simpler pattern for title extraction
    if not stories:
        # look for h2 tags with Chinese text near ycombinator links
        h2_pattern = re.compile(r'<h2[^>]*class="[^"]*font-semibold[^"]*"[^>]*>(.*?)</h2>', re.DOTALL)
        for match in h2_pattern.finditer(html):
            title = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            if title and len(title) > 5:
                stories.append({
                    "id": "",
                    "title_zh": title,
                    "hn_url": "",
                    "source": "zeli_html",
                })

    logger.info(f"  fetched {len(stories)} zeli articles")
    return stories[:top_n], "zeli_html"


def fetch_zeli_top(top_n: int = TOP_N) -> Tuple[List[Dict], str]:
    """try zeli HTML, skip on failure."""
    try:
        stories, source = _fetch_zeli_html(top_n)
        if stories:
            return stories, source
    except Exception as e:
        logger.warning(f"  zeli fetch failed: {e}")

    logger.info("  zeli unavailable, will use HN data only")
    return [], "zeli_skipped"


# ------------------------------------------------------------------ #
# archive + page generation
# ------------------------------------------------------------------ #

def save_run(archive: JsonArchive, hn_stories: List[Dict], zeli_stories: List[Dict],
             hn_source: str, zeli_source: str):
    """append a run entry to the archive."""
    now = datetime.now(timezone.utc)
    run = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.isoformat(),
        "hn_source": hn_source,
        "zeli_source": zeli_source,
        "hn_stories": hn_stories,
        "zeli_stories": zeli_stories,
    }

    runs = archive.get("runs", [])
    if not isinstance(runs, list):
        runs = []

    # keep last 30 days of runs
    cutoff = (now - timedelta(days=30)).isoformat()
    runs = [r for r in runs if r.get("time", "") >= cutoff]
    runs.append(run)

    archive.set("runs", runs)
    archive.set("last_updated", now.isoformat())
    archive.save()

    logger.info(f"archive saved: {len(runs)} run(s)")


def generate_page(hn_stories: List[Dict], zeli_stories: List[Dict],
                  hn_source: str, zeli_source: str):
    """generate docs_hn/index.md for GitHub Pages."""
    now = datetime.now(timezone.utc)
    lines = [
        "# HN Daily Top 10",
        "",
        f"> Updated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"> HN source: `{hn_source}` | zeli source: `{zeli_source}`",
        "",
    ]

    if hn_stories:
        lines.append("## Hacker News Top 10")
        lines.append("")
        lines.append("| # | Score | Title | Comments |")
        lines.append("|---|-------|-------|----------|")
        for i, s in enumerate(hn_stories, 1):
            title = s.get("title", "untitled")
            url = s.get("url", "")
            score = s.get("score", "?")
            comments = s.get("comments", "?")
            hn_link = f"https://news.ycombinator.com/item?id={s['id']}" if s.get("id") else ""
            title_md = f"[{title}]({url})" if url else title
            comments_md = f"[{comments}]({hn_link})" if hn_link else str(comments)
            lines.append(f"| {i} | {score} | {title_md} | {comments_md} |")
        lines.append("")

    if zeli_stories:
        lines.append("## zeli.app 中文摘要")
        lines.append("")
        lines.append("| # | 标题 | 链接 |")
        lines.append("|---|------|------|")
        for i, s in enumerate(zeli_stories, 1):
            title = s.get("title_zh", "")
            hn_url = s.get("hn_url", "")
            title_md = f"[{title}]({hn_url})" if hn_url else title
            lines.append(f"| {i} | {title_md} | [HN]({hn_url}) |" if hn_url else f"| {i} | {title} | — |")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by [lgpac](https://github.com/host452b/lgpac_cli) at {now.isoformat()}*")
    lines.append("")

    path = Path(DOCS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"page generated: {DOCS_FILE}")


# ------------------------------------------------------------------ #
# email
# ------------------------------------------------------------------ #

def send_hn_email(hn_stories: List[Dict], zeli_stories: List[Dict],
                  hn_source: str, zeli_source: str) -> bool:
    """send daily digest email."""
    if not hn_stories:
        return False

    now = datetime.now(timezone.utc)
    subject = f"HN Top 10 — {now.strftime('%Y-%m-%d')}"

    rows = []
    for i, s in enumerate(hn_stories, 1):
        title = s.get("title", "")
        url = s.get("url", "")
        score = s.get("score", "?")
        comments = s.get("comments", "?")
        hn_link = f"https://news.ycombinator.com/item?id={s['id']}" if s.get("id") else "#"
        rows.append(
            f'<tr><td>{i}</td><td>{score}</td>'
            f'<td><a href="{url}">{title}</a></td>'
            f'<td><a href="{hn_link}">{comments}</a></td></tr>'
        )

    zeli_rows = []
    for i, s in enumerate(zeli_stories, 1):
        title = s.get("title_zh", "")
        hn_url = s.get("hn_url", "")
        zeli_rows.append(
            f'<tr><td>{i}</td><td><a href="{hn_url}">{title}</a></td></tr>'
        )

    html = f"""
    <h2>HN Top 10 — {now.strftime('%Y-%m-%d')}</h2>
    <p style="color:#666">source: {hn_source} | zeli: {zeli_source}</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
      <tr><th>#</th><th>Score</th><th>Title</th><th>Comments</th></tr>
      {''.join(rows)}
    </table>
    """

    if zeli_rows:
        html += f"""
        <h3>zeli.app 中文摘要</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
          <tr><th>#</th><th>标题</th></tr>
          {''.join(zeli_rows)}
        </table>
        """

    return send_email(subject, html)


# ------------------------------------------------------------------ #
# main entry
# ------------------------------------------------------------------ #

def run_monitor(
    top_n: int = TOP_N,
    notify: bool = False,
    page: bool = False,
) -> Tuple[List[Dict], List[Dict], str, str]:
    """
    fetch HN top-N + zeli top-N with fallback chain.
    returns (hn_stories, zeli_stories, hn_source, zeli_source).
    """
    archive = JsonArchive(ARCHIVE_FILE)
    archive.load()

    hn_stories, hn_source = fetch_hn_top(top_n, archive)
    zeli_stories, zeli_source = fetch_zeli_top(top_n)

    # save to archive
    save_run(archive, hn_stories, zeli_stories, hn_source, zeli_source)

    # generate GitHub Pages
    if page:
        generate_page(hn_stories, zeli_stories, hn_source, zeli_source)

    # send email
    if notify:
        ok = send_hn_email(hn_stories, zeli_stories, hn_source, zeli_source)
        if ok:
            logger.info("email sent")
        else:
            logger.debug("email skipped (not configured or no stories)")

    return hn_stories, zeli_stories, hn_source, zeli_source
