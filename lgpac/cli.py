"""
lgpac CLI - crawler & browser automation toolkit.

commands:
    lgpac crawl         full API crawl (show list + details)
    lgpac traverse      recursive browser DFS of the entire site
    lgpac replay        execute a YAML playbook
    lgpac info          show site info and categories
    lgpac schedule      run crawl at fixed intervals
"""
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

from lgpac import __version__
from lgpac.config import SiteConfig

app = typer.Typer(
    name="lgpac",
    help="crawler & browser automation toolkit",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


def _make_config(debug: bool = False, output: str = "data") -> SiteConfig:
    return SiteConfig(debug=debug, output_dir=output)


# ------------------------------------------------------------------ #
# crawl - API-based data extraction
# ------------------------------------------------------------------ #

@app.command()
def crawl(
    quick: bool = typer.Option(False, "--quick", "-q", help="list only, skip detail enrichment"),
    rss: bool = typer.Option(False, "--rss", help="update RSS.md with crawl results"),
    debug: bool = typer.Option(False, "--debug", "-d", help="enable screenshots and verbose logging"),
    output: str = typer.Option("data", "--output", "-o", help="output directory"),
):
    """crawl all shows via API. use --quick for list-only mode."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.spider import LgpacSpider
    spider = LgpacSpider(config=config)
    shows, diff = spider.crawl_all(fetch_details=not quick)

    if rss:
        from lgpac.rss import update_rss
        update_rss(shows, diff)
        console.print("[green]RSS.md updated[/green]")

    table = Table(title=f"{len(shows)} shows")
    table.add_column("category", style="cyan", width=8)
    table.add_column("name", style="bold")
    table.add_column("date", style="green")
    table.add_column("price", style="yellow", justify="right")
    table.add_column("sessions", justify="right")

    for s in shows:
        cat = s.category.display_name if s.category else ""
        price = s.min_price_info.display if s.min_price_info else ""
        table.add_row(cat, s.name, s.show_date, price, str(s.session_count))

    console.print(table)
    console.print(f"\n[green]data saved to:[/green] {config.output_dir}/latest/")


# ------------------------------------------------------------------ #
# monitor - affordable ticket tracking
# ------------------------------------------------------------------ #

@app.command()
def monitor(
    price: float = typer.Option(120.0, "--price", "-p", help="max ticket price to watch"),
    rss: bool = typer.Option(False, "--rss", help="update RSS.md"),
    page: bool = typer.Option(False, "--page", help="generate docs_lgpac/index.md for GitHub Pages"),
    notify: bool = typer.Option(False, "--notify", "-n", help="send webhook notification"),
    email: bool = typer.Option(False, "--email", help="send email for NEW shows (needs SMTP env vars)"),
    debug: bool = typer.Option(False, "--debug", "-d"),
    output: str = typer.Option("data", "--output", "-o"),
):
    """monitor ticket availability under a price threshold."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.spider import LgpacSpider
    from lgpac.monitor import analyze_shows, format_alerts_text, format_alerts_markdown, send_email_alert
    from lgpac.notify import send_webhook

    spider = LgpacSpider(config=config)
    shows, diff = spider.crawl_all(fetch_details=True)

    alerts = analyze_shows(shows, max_price=price)

    text = format_alerts_text(alerts, price)
    console.print(text)

    if rss:
        from lgpac.rss import update_rss
        monitor_md = format_alerts_markdown(alerts, price)
        update_rss(shows, diff, extra_section=monitor_md)
        console.print("\n[green]RSS.md updated[/green]")

    if page:
        from lgpac.page import generate_page
        generate_page(shows, alerts, max_price=price, diff=diff)
        console.print("[green]docs_lgpac/index.md generated[/green]")

    if notify:
        in_stock = [a for a in alerts if a.status in ("new", "available", "back_in_stock")]
        if in_stock:
            send_webhook(text)
            console.print("[green]webhook sent[/green]")
        else:
            console.print("[dim]no in-stock alerts, webhook skipped[/dim]")

    if email:
        ok = send_email_alert(alerts, price)
        if ok:
            console.print("[green]email sent[/green]")
        else:
            console.print("[dim]email skipped (no new shows or not configured)[/dim]")


# ------------------------------------------------------------------ #
# traverse - recursive browser DFS
# ------------------------------------------------------------------ #

@app.command()
def traverse(
    max_depth: int = typer.Option(3, "--depth", help="max traversal depth"),
    max_pages: int = typer.Option(200, "--pages", help="max pages to visit"),
    archs: bool = typer.Option(False, "--archs", "-a", help="save to archs/ and overwrite each run"),
    debug: bool = typer.Option(False, "--debug", "-d", help="enable screenshots"),
    output: str = typer.Option("data", "--output", "-o", help="output directory"),
):
    """recursively traverse the site via browser, recording all page content."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    out_dir = "archs" if archs else None
    from lgpac.browser.traversal import SiteTraverser
    traverser = SiteTraverser(
        config=config,
        max_depth=max_depth,
        max_pages=max_pages,
        output_dir=out_dir,
        overwrite=archs,
    )
    root = traverser.traverse()

    _print_tree(root)
    target = "archs/" if archs else f"{config.output_dir}/traversal/"
    console.print(f"\n[green]traversal data saved to:[/green] {target}")


def _print_tree(node, indent: int = 0):
    """pretty-print the site tree."""
    prefix = "  " * indent
    icon = "📄"
    if node.trigger.startswith("tab:"):
        icon = "🏷️"
    elif node.trigger.startswith("card:"):
        icon = "🎭"
    elif node.trigger.startswith("nav:"):
        icon = "📌"
    elif node.trigger == "root":
        icon = "🏠"

    label = node.title or node.trigger
    status = ""
    if node.error:
        status = f" [dim]({node.error})[/dim]"

    console.print(f"{prefix}{icon} {label}{status}")

    for child in node.children:
        _print_tree(child, indent + 1)


# ------------------------------------------------------------------ #
# replay - execute YAML playbooks
# ------------------------------------------------------------------ #

@app.command()
def replay(
    playbook: str = typer.Argument(help="path to YAML playbook file"),
    debug: bool = typer.Option(False, "--debug", "-d", help="enable screenshots"),
    output: str = typer.Option("data", "--output", "-o", help="output directory"),
):
    """execute a YAML playbook for precise browser automation."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.browser.replay import PlaybookRunner
    runner = PlaybookRunner(config=config)
    results = runner.run_file(playbook)

    table = Table(title=f"replay: {Path(playbook).stem}")
    table.add_column("#", justify="right", width=4)
    table.add_column("action", style="cyan")
    table.add_column("status")
    table.add_column("detail")

    for r in results:
        status = "[green]✓[/green]" if r.success else "[red]✗[/red]"
        detail = ""
        if r.data and isinstance(r.data, dict):
            detail = str(r.data)[:60]
        if r.error:
            detail = f"[red]{r.error[:60]}[/red]"
        table.add_row(str(r.step_index), r.action, status, detail)

    console.print(table)
    console.print(f"\n[green]results saved to:[/green] {config.output_dir}/replay/")


# ------------------------------------------------------------------ #
# info - site overview
# ------------------------------------------------------------------ #

@app.command()
def info(
    debug: bool = typer.Option(False, "--debug", "-d"),
):
    """show site info, categories, and navigation structure."""
    _setup_logging(debug)
    config = _make_config(debug)

    from lgpac.spider import LgpacSpider
    spider = LgpacSpider(config=config)

    shop_config = spider.crawl_shop_config()
    categories = spider.crawl_categories()

    console.print(f"\n[bold]site:[/bold] {config.base_url}")

    if shop_config:
        console.print(f"[bold]shop:[/bold] {shop_config.shop_name}")
        console.print(f"[bold]ICP:[/bold]  {shop_config.icp_license}")

        cat_table = Table(title="frontend categories")
        cat_table.add_column("name", style="cyan")
        cat_table.add_column("codes", style="dim")
        for c in shop_config.frontend_categories:
            cat_table.add_row(c.name, ", ".join(c.category_codes))
        console.print(cat_table)

        nav_table = Table(title="bottom navigation")
        nav_table.add_column("name", style="cyan")
        nav_table.add_column("path")
        for n in shop_config.bottom_navigations:
            nav_table.add_row(n["name"], n["path"])
        console.print(nav_table)

    if categories:
        bt_table = Table(title=f"backend categories ({len(categories)})")
        bt_table.add_column("code", justify="right", width=5)
        bt_table.add_column("name", style="cyan")
        bt_table.add_column("key", style="dim")
        for c in sorted(categories, key=lambda x: x.seq):
            bt_table.add_row(str(c.code), c.display_name, c.name)
        console.print(bt_table)


# ------------------------------------------------------------------ #
# schedule - periodic crawling
# ------------------------------------------------------------------ #

@app.command()
def schedule(
    interval: int = typer.Option(60, "--interval", "-i", help="crawl interval in minutes"),
    debug: bool = typer.Option(False, "--debug", "-d"),
    output: str = typer.Option("data", "--output", "-o"),
):
    """run the crawler at fixed intervals. Ctrl+C to stop."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.scheduler import CrawlScheduler
    scheduler = CrawlScheduler(interval_minutes=interval, config=config)

    console.print(f"[bold]scheduling crawl every {interval} minutes[/bold]")
    console.print("press Ctrl+C to stop\n")
    scheduler.start()


# ------------------------------------------------------------------ #
# lgycp - weixin article monitor
# ------------------------------------------------------------------ #

@app.command()
def lgycp(
    query: str = typer.Option("", "--query", "-q", help="search query (or set LGYCP_QUERY env var)"),
    notify: bool = typer.Option(False, "--notify", "-n", help="send email on new articles"),
    page: bool = typer.Option(False, "--page", help="generate docs_lgycp/index.md"),
    debug: bool = typer.Option(False, "--debug", "-d"),
):
    """monitor weixin articles for activity/enrollment notices."""
    _setup_logging(debug)

    from lgpac.lgycp import run_monitor
    from lgpac.archive import JsonArchive

    new_articles = run_monitor(query=query, notify=notify, page=page)

    if new_articles:
        table = Table(title=f"{len(new_articles)} new article(s)")
        table.add_column("title", style="bold")
        table.add_column("source", style="dim")
        for a in new_articles:
            table.add_row(a["title"], a.get("source", ""))
        console.print(table)
    else:
        console.print("[dim]no new articles matching keywords[/dim]")

    archive = JsonArchive("archs_lgycp/archive.json", key_field="titles")
    archive.load()
    console.print(f"[green]archive: {len(archive.keys())} articles[/green]")
    if page:
        console.print("[green]docs_lgycp/index.md generated[/green]")


# ------------------------------------------------------------------ #
# xbirds - X/Twitter post tracker
# ------------------------------------------------------------------ #

@app.command()
def xbirds(
    notify: bool = typer.Option(False, "--notify", "-n", help="send email on new posts"),
    page: bool = typer.Option(False, "--page", help="generate docs_xbirds/index.md"),
    hours: int = typer.Option(24, "--hours", help="lookback window in hours (default 24, use 168 for 1 week)"),
    stage: Optional[str] = typer.Option(None, "--stage", "-s", help="only fetch specific stages, e.g. '0,1' or '0-2'"),
    add: Optional[str] = typer.Option(None, "--add", help="add a username to track"),
    remove: Optional[str] = typer.Option(None, "--remove", help="remove a tracked username"),
    debug: bool = typer.Option(False, "--debug", "-d"),
):
    """track X/Twitter posts from followed users (tracked.yml)."""
    _setup_logging(debug)

    from lgpac.xbirds import run_monitor, add_user, remove_user, get_usernames, parse_tweet_date

    if add:
        name = add.lstrip("@")
        if add_user(name):
            console.print(f"[green]added @{name}[/green]")
        else:
            console.print(f"[dim]@{name} already tracked[/dim]")
        return

    if remove:
        name = remove.lstrip("@")
        if remove_user(name):
            console.print(f"[yellow]removed @{name}[/yellow]")
        else:
            console.print(f"[dim]@{name} not in list[/dim]")
        return

    usernames = get_usernames()
    console.print(f"tracking {len(usernames)} users\n")

    # parse --stage filter
    stage_filter = None
    if stage:
        stages = set()
        for part in stage.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                stages.update(range(int(lo), int(hi) + 1))
            else:
                stages.add(int(part))
        stage_filter = stages

    new_posts, warnings = run_monitor(notify=notify, page=page, recent_hours=hours, stage_filter=stage_filter)

    if new_posts:
        table = Table(title=f"{len(new_posts)} new post(s)")
        table.add_column("user", style="cyan")
        table.add_column("post", style="bold")
        table.add_column("date", style="dim")
        for p in new_posts:
            table.add_row(f"@{p['username']}", p['text'][:60], parse_tweet_date(p.get('created_at', '')))
        console.print(table)
    else:
        console.print("[dim]no new posts[/dim]")

    if warnings:
        console.print(f"\n[yellow]⚠ {len(warnings)} account(s) with issues:[/yellow]")
        for w in warnings:
            console.print(f"  [dim]@{w['username']} ({w['name']}): {w['issue']}[/dim]")

    if page:
        console.print(f"\n[green]docs_xbirds/index.md generated[/green]")


# ------------------------------------------------------------------ #
# hn - Hacker News + zeli daily top-10
# ------------------------------------------------------------------ #

@app.command()
def hn(
    top: int = typer.Option(10, "--top", "-t", help="number of stories to fetch"),
    notify: bool = typer.Option(False, "--notify", "-n", help="send email digest"),
    page: bool = typer.Option(False, "--page", help="generate docs_hn/index.md"),
    debug: bool = typer.Option(False, "--debug", "-d"),
):
    """daily HN + zeli.app top-10 with fallback chain (Firebase → Algolia → RSS → archive)."""
    _setup_logging(debug)

    from lgpac.hn import run_monitor

    hn_stories, zeli_stories, hn_source, zeli_source = run_monitor(
        top_n=top, notify=notify, page=page,
    )

    console.print(f"\n[bold]HN source:[/bold] {hn_source}")
    console.print(f"[bold]zeli source:[/bold] {zeli_source}\n")

    if hn_stories:
        table = Table(title=f"HN Top {len(hn_stories)}")
        table.add_column("#", justify="right", width=3)
        table.add_column("Score", justify="right", style="yellow", width=6)
        table.add_column("Title", style="bold")
        table.add_column("Comments", justify="right", style="dim", width=8)
        for i, s in enumerate(hn_stories, 1):
            table.add_row(str(i), str(s.get("score", "?")), s["title"], str(s.get("comments", "?")))
        console.print(table)
    else:
        console.print("[red]no HN stories fetched (all sources failed)[/red]")

    if zeli_stories:
        console.print()
        zt = Table(title=f"zeli.app 中文摘要 ({len(zeli_stories)})")
        zt.add_column("#", justify="right", width=3)
        zt.add_column("标题", style="bold")
        for i, s in enumerate(zeli_stories, 1):
            zt.add_row(str(i), s.get("title_zh", ""))
        console.print(zt)
    else:
        console.print("[dim]zeli: unavailable or skipped[/dim]")

    if page:
        console.print("[green]docs_hn/index.md generated[/green]")


# ------------------------------------------------------------------ #
# version
# ------------------------------------------------------------------ #

@app.command()
def version():
    """show version."""
    console.print(f"lgpac {__version__}")


if __name__ == "__main__":
    app()
