"""
weixin article monitor with fallback search providers.
primary: sogou weixin search
fallback: baidu / bing site:mp.weixin.qq.com

all providers return normalized format: [{"title", "url", "source", "pub_date"}]
"""
import os
import re
import logging
import html as html_mod
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any

from lgpac.archive import JsonArchive
from lgpac.notify import send_email, build_html_email

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
# provider: sogou (primary)
# ------------------------------------------------------------------ #

def _fetch_sogou(query: str) -> List[Dict[str, str]]:
    import requests

    url = f"https://weixin.sogou.com/weixin?type=2&s_from=input&ie=utf8&query={query}"
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
    time_pattern = re.compile(r"timeConvert\('(\d+)'\)")
    timestamps = [int(m.group(1)) for m in time_pattern.finditer(resp.text)]

    results = []
    for m in title_pattern.finditer(resp.text):
        link, idx, raw = m.group(1), m.group(2), m.group(3)
        title = _clean_html(raw)
        if not title:
            continue
        if link.startswith("/link?"):
            link = "https://weixin.sogou.com" + link

        pub_date = ""
        idx_int = int(idx)
        if idx_int < len(timestamps):
            pub_date = datetime.fromtimestamp(timestamps[idx_int], tz=timezone.utc).strftime("%Y-%m-%d")

        results.append({
            "title": title, "url": html_mod.unescape(link),
            "index": idx, "source": "", "pub_date": pub_date,
        })

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
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=15)
    resp.raise_for_status()

    pattern = re.compile(
        r'<h3[^>]*class="[^"]*c-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    results = []
    for m in pattern.finditer(resp.text):
        link = html_mod.unescape(m.group(1))
        title = _clean_html(m.group(2))
        if title and query.split()[0] in title:
            results.append({"title": title, "url": link, "source": "", "pub_date": ""})
    return results


# ------------------------------------------------------------------ #
# provider: bing (fallback 2)
# ------------------------------------------------------------------ #

def _fetch_bing(query: str) -> List[Dict[str, str]]:
    import requests

    url = f"https://www.bing.com/search?q={query}+site%3Amp.weixin.qq.com&count=20"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=15)
    resp.raise_for_status()

    pattern = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL)
    results = []
    for m in pattern.finditer(resp.text):
        link = html_mod.unescape(m.group(1))
        title = _clean_html(m.group(2))
        if title and query.split()[0] in title:
            results.append({"title": title, "url": link, "source": "", "pub_date": ""})
    return results


# ------------------------------------------------------------------ #
# fetch with fallback
# ------------------------------------------------------------------ #

PROVIDERS = [("sogou", _fetch_sogou), ("baidu", _fetch_baidu), ("bing", _fetch_bing)]


def fetch_articles(query: str = "") -> List[Dict[str, str]]:
    if not query:
        query = os.environ.get("LGYCP_QUERY", "").strip() or "临港少年宫"
    for name, fetcher in PROVIDERS:
        try:
            articles = fetcher(query)
            if articles:
                logger.info(f"[{name}] fetched {len(articles)} articles")
                return articles
            logger.warning(f"[{name}] returned 0 results, trying fallback")
        except Exception as e:
            logger.warning(f"[{name}] failed: {type(e).__name__} - {e}, trying fallback")
    logger.error("all providers failed")
    return []


# ------------------------------------------------------------------ #
# filter / archive (uses shared JsonArchive)
# ------------------------------------------------------------------ #

def filter_relevant(articles: List[Dict], year_range: tuple = (2026, 2056)) -> List[Dict]:
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
    archive = JsonArchive(ARCHIVE_FILE, key_field="titles")
    archive.load()
    now = datetime.now(timezone.utc).isoformat()

    new_articles = []
    for article in articles:
        title = article["title"]
        if not archive.has(title):
            new_articles.append(article)
            archive.add(title, {
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "pub_date": article.get("pub_date", ""),
                "found_at": now,
            })

    if new_articles:
        archive.set("last_updated", now)
        archive.set("total_count", len(archive.keys()))
        archive.save()
        logger.info(f"archived {len(new_articles)} new (total: {len(archive.keys())})")
    else:
        logger.info("no new articles")

    return new_articles


# ------------------------------------------------------------------ #
# page generation
# ------------------------------------------------------------------ #

def generate_page(output_path: str = "docs_lgycp/index.md"):
    archive = JsonArchive(ARCHIVE_FILE, key_field="titles")
    archive.load()
    titles = archive.get("titles", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = ["# 📢 Article Monitor", "", f"> updated: {now} · {len(titles)} articles archived", ""]

    if not titles:
        lines.append("*waiting for first run...*")
    else:
        lines.append("| # | Title | Source | Published | Found |")
        lines.append("|---|-------|--------|-----------|-------|")
        sorted_items = sorted(titles.items(), key=lambda x: x[1].get("found_at", ""), reverse=True)
        for i, (title, info) in enumerate(sorted_items, 1):
            url = info.get("url", "")
            source = info.get("source", "")
            pub_date = info.get("pub_date", "")
            found = info.get("found_at", "")[:10]
            title_esc = title.replace("|", "∣")
            title_cell = f"[{title_esc}]({url})" if url else title_esc
            lines.append(f"| {i} | {title_cell} | {source} | {pub_date} | {found} |")

    lines.append("")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"lgycp page generated: {path}")


# ------------------------------------------------------------------ #
# email (uses shared notify)
# ------------------------------------------------------------------ #

def send_email_alert(new_articles: List[Dict]) -> bool:
    if not new_articles:
        return False

    rows = []
    for a in new_articles:
        title = a["title"]
        url = a.get("url", "#")
        source = a.get("source", "")
        pub_date = a.get("pub_date", "")
        rows.append([
            f'<a href="{url}" style="color:#1a73e8;text-decoration:none;">{title}</a>',
            f'<span style="color:#666;">{source}</span>',
            f'<span style="color:#888;">{pub_date}</span>',
        ])

    html = build_html_email(
        title=f"📢 {len(new_articles)} new article(s)",
        heading_color="#2da44e",
        table_headers=["Title", "Source", "Published"],
        table_rows=rows,
    )
    return send_email(f"[lgycp] {len(new_articles)} new article(s)", html)


# ------------------------------------------------------------------ #
# run
# ------------------------------------------------------------------ #

def run_monitor(query: str = "临港少年宫", notify: bool = False, page: bool = False) -> List[Dict]:
    articles = fetch_articles(query)
    relevant = filter_relevant(articles)
    new_articles = check_and_archive(relevant)

    if page:
        generate_page()

    if new_articles and notify:
        send_email_alert(new_articles)

    return new_articles


# ------------------------------------------------------------------ #
# helpers
# ------------------------------------------------------------------ #

def _clean_html(raw: str) -> str:
    text = re.sub(r'<[^>]+>', '', raw)
    text = re.sub(r'<!--.*?-->', '', text)
    return html_mod.unescape(text).strip()
