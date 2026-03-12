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
    page: bool = typer.Option(False, "--page", help="generate docs/index.html for GitHub Pages"),
    notify: bool = typer.Option(False, "--notify", "-n", help="send webhook notification"),
    debug: bool = typer.Option(False, "--debug", "-d"),
    output: str = typer.Option("data", "--output", "-o"),
):
    """monitor ticket availability under a price threshold."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.spider import LgpacSpider
    from lgpac.monitor import analyze_shows, format_alerts_text, format_alerts_markdown, send_webhook

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
        console.print("[green]docs/index.md generated[/green]")

    if notify:
        in_stock = [a for a in alerts if a.status in ("new", "available", "back_in_stock")]
        if in_stock:
            send_webhook(text)
            console.print("[green]notification sent[/green]")
        else:
            console.print("[dim]no in-stock alerts, notification skipped[/dim]")


# ------------------------------------------------------------------ #
# traverse - recursive browser DFS
# ------------------------------------------------------------------ #

@app.command()
def traverse(
    max_depth: int = typer.Option(3, "--depth", help="max traversal depth"),
    max_pages: int = typer.Option(30, "--pages", help="max pages to visit"),
    debug: bool = typer.Option(False, "--debug", "-d", help="enable screenshots"),
    output: str = typer.Option("data", "--output", "-o", help="output directory"),
):
    """recursively traverse the site via browser, recording page structure."""
    _setup_logging(debug)
    config = _make_config(debug, output)

    from lgpac.browser.traversal import SiteTraverser
    traverser = SiteTraverser(config=config, max_depth=max_depth, max_pages=max_pages)
    root = traverser.traverse()

    _print_tree(root)
    console.print(f"\n[green]traversal data saved to:[/green] {config.output_dir}/traversal/")


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
# version
# ------------------------------------------------------------------ #

@app.command()
def version():
    """show version."""
    console.print(f"lgpac {__version__}")


if __name__ == "__main__":
    app()
