# lgpac

A crawler & browser automation toolkit for a performing arts ticketing platform. Built for learning and research purposes.

> **DISCLAIMER / 免责声明**
>
> This project is for **educational and research purposes only**. It is not intended for commercial use, unauthorized data collection, or any activity that violates applicable laws or the target website's terms of service. The authors assume no liability for any misuse. Use at your own risk and responsibility.
>
> 本项目**仅供学习与研究使用**。不得用于商业目的、未经授权的数据采集，或任何违反相关法律法规及目标网站服务条款的行为。作者不对任何滥用行为承担责任。使用者需自行承担风险与责任。

## Features

- **API Crawl** — extract show listings, sessions, seat plans, and pricing via public API
- **Recursive Traversal** (`-r`) — browser-based DFS of the entire site tree, recording page structure and metadata at each node
- **Replay Playbooks** — deterministic YAML-driven browser automation for repeatable query flows (no AI involved)
- **Debug Mode** (`-d`) — screenshots at each step for human inspection
- **Diff Tracking** — automatic change detection between crawl runs (new/removed/changed shows)
- **Scheduled Crawling** — built-in interval-based scheduler

## Requirements

- Python >= 3.9
- Playwright (with Chromium or system Chrome)

## Install

```bash
# install dependencies
pip install requests typer rich pyyaml playwright

# install playwright browser (or use system Chrome as fallback)
python -m playwright install chromium
```

## Usage

All commands run via the entry point script:

```bash
python lgpac_cli.py <command> [options]
```

### Crawl — API data extraction

```bash
# full crawl: show list + detail enrichment (sessions, prices, notes)
python lgpac_cli.py crawl

# quick mode: list only, no detail API calls
python lgpac_cli.py crawl -q

# with debug screenshots
python lgpac_cli.py crawl -d
```

Output saved to `data/latest/`:
- `shows.json` — all shows with full detail
- `shop_config.json` — site configuration
- `categories.json` — show categories
- `diff.json` — changes since last run

Add `--rss` to also update `RSS.md`:

```bash
python lgpac_cli.py crawl --rss
```

### Traverse — recursive site exploration

```bash
# DFS traversal, max depth 3, up to 30 pages
python lgpac_cli.py traverse

# custom depth and page limit, with screenshots
python lgpac_cli.py traverse --depth 2 --pages 15 -d
```

Output saved to `data/traversal/<timestamp>/`:
- `site_tree.json` — hierarchical page structure
- `all_pages.json` — flat list of visited pages

### Replay — YAML playbook execution

```bash
# run a playbook
python lgpac_cli.py replay playbooks/check_show.yaml

# with debug screenshots
python lgpac_cli.py replay playbooks/browse_categories.yaml -d
```

Playbook format:

```yaml
name: example_flow
steps:
  - action: navigate
    url: http://example.com
  - action: dismiss_popup
  - action: click_text
    text: "some button"
  - action: screenshot
    name: result
  - action: extract_meta
```

Available actions: `navigate`, `wait`, `dismiss_popup`, `click`, `click_text`, `scroll_bottom`, `screenshot`, `extract`, `extract_meta`, `assert_visible`, `assert_url_contains`, `go_back`, `type_text`

### Info — site overview

```bash
python lgpac_cli.py info
```

### Schedule — periodic crawling

```bash
# crawl every 60 minutes
python lgpac_cli.py schedule -i 60
```

## Project Structure

```
├── lgpac_cli.py              # entry point
├── pyproject.toml             # packaging config
├── lgpac/                     # core package
│   ├── cli.py                 # typer CLI commands
│   ├── config.py              # site config & API routing
│   ├── client.py              # HTTP client (retry, rate-limit)
│   ├── api.py                 # public API wrappers
│   ├── models.py              # data models
│   ├── storage.py             # JSON persistence + diff
│   ├── spider.py              # crawl orchestration
│   ├── scheduler.py           # interval scheduler
│   └── browser/
│       ├── engine.py          # Playwright lifecycle & screenshots
│       ├── actions.py         # smart action library (popup, click, scroll)
│       ├── traversal.py       # recursive DFS traversal
│       └── replay.py          # YAML playbook engine
├── playbooks/                 # replay definitions
│   ├── check_show.yaml
│   └── browse_categories.yaml
├── SITE_STRUCTURE.md          # API structure & patterns reference
├── RSS.md                     # auto-updated show feed
├── .github/workflows/crawl.yml
└── data/                      # output (git-ignored except latest)
```

## GitHub Actions

A workflow runs `crawl --rss` every 4 hours and commits results automatically.

- Workflow: `.github/workflows/crawl.yml`
- Schedule: `cron: "0 */4 * * *"` (every 4h)
- Also supports manual trigger via `workflow_dispatch`
- Commits `RSS.md` + `data/latest/` back to the repo

`RSS.md` is incrementally updated — each run prepends a new entry with the current show table and a diff summary.

## Configuration

Set `LGPAC_TARGET_URL` environment variable to override the default target:

```bash
export LGPAC_TARGET_URL="http://your-target-site.example.com"
```

For GitHub Actions, add it as a repository secret or variable.

## License

MIT — see [DISCLAIMER](#lgpac) above.
