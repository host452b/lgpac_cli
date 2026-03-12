# lgpac

A crawler & monitoring toolkit for performing arts venues and youth activity centers. Built for learning and research purposes.

> **DISCLAIMER / 免责声明**
>
> This project is for **educational and research purposes only**. It is not intended for commercial use, unauthorized data collection, or any activity that violates applicable laws or the target website's terms of service. The authors assume no liability for any misuse. Use at your own risk and responsibility.
>
> 本项目**仅供学习与研究使用**。不得用于商业目的、未经授权的数据采集，或任何违反相关法律法规及目标网站服务条款的行为。作者不对任何滥用行为承担责任。使用者需自行承担风险与责任。

## Features

- **Show Monitor** — track ticket prices and real-time stock via public API, alert on new shows or restocked cheapest tiers
- **WeChat Article Monitor** — watch for enrollment/activity notices from WeChat public accounts via search engines (sogou → baidu → bing fallback chain)
- **Recursive Traversal** — browser-based DFS of site tree, archiving full HTML + metadata per page
- **Replay Playbooks** — deterministic YAML-driven browser automation (no AI involved)
- **GitHub Pages Dashboard** — auto-generated `docs/index.md` with ticket listing, stock status, and price breakdown
- **Email Alerts** — notifications for new shows with affordable tickets, or new activity articles
- **Debug Mode** (`-d`) — screenshots at each step for human inspection

## Requirements

- Python >= 3.9
- Playwright (optional, only for `traverse` and `replay` commands)

## Install

```bash
pip install requests typer rich pyyaml

# optional: for browser commands (traverse, replay)
pip install playwright && python -m playwright install chromium
```

## Commands

```bash
python lgpac_cli.py <command> [options]
```

### monitor — ticket price & stock monitoring

```bash
# monitor shows with tickets under ¥120
python lgpac_cli.py monitor --price 120

# full pipeline: monitor + update RSS + generate page + webhook + email
python lgpac_cli.py monitor --price 120 --rss --page --notify --email
```

Checks real-time stock via `canBuyCount` from the dynamic API. Sends email only when:
- a **new show** appears with affordable tickets in stock
- an **old show's cheapest tier** comes back in stock (was sold out, now available again)

### lgycp — WeChat article monitor

```bash
# check for new activity/enrollment articles
python lgpac_cli.py lgycp

# with email notification on new articles
python lgpac_cli.py lgycp --notify
```

Searches WeChat articles via sogou, with automatic fallback to baidu and bing if sogou is blocked. Filters by keywords: 报名, 通知, 开课, 招生, 招募, 儿童剧, 舞蹈, 体能, etc.

Archives are stored in `archs_lgycp/archive.json` — only new (unseen) articles trigger notifications.

### crawl — raw API data extraction

```bash
python lgpac_cli.py crawl            # full crawl with detail enrichment
python lgpac_cli.py crawl -q         # quick: list only
python lgpac_cli.py crawl --rss      # also update RSS.md
```

### traverse — recursive site exploration

```bash
python lgpac_cli.py traverse                         # default DFS
python lgpac_cli.py traverse --archs --depth 2 -d    # archive to archs/, with screenshots
```

Archives each page as `.html` + `.json` in `archs/pages/`.

### replay — YAML playbook execution

```bash
python lgpac_cli.py replay playbooks/check_show.yaml -d
```

Available actions: `navigate`, `wait`, `dismiss_popup`, `click`, `click_text`, `scroll_bottom`, `screenshot`, `extract`, `extract_meta`, `assert_visible`, `assert_url_contains`, `go_back`, `type_text`

### info — site overview

```bash
python lgpac_cli.py info
```

### schedule — periodic local crawling

```bash
python lgpac_cli.py schedule -i 60
```

## Project Structure

```
├── lgpac_cli.py              # entry point
├── pyproject.toml             # packaging
├── lgpac/
│   ├── cli.py                 # typer CLI commands
│   ├── config.py              # site config & API routing
│   ├── client.py              # HTTP client (retry, rate-limit)
│   ├── api.py                 # public API wrappers
│   ├── models.py              # data models (Show, Session, SeatPlan)
│   ├── monitor.py             # ticket monitor + email alerts
│   ├── lgycp.py               # WeChat article monitor (sogou/baidu/bing)
│   ├── page.py                # docs/index.md generator
│   ├── rss.py                 # RSS.md incremental feed
│   ├── spider.py              # crawl orchestration
│   ├── storage.py             # JSON persistence + diff
│   ├── scheduler.py           # interval scheduler
│   └── browser/
│       ├── engine.py          # Playwright lifecycle & screenshots
│       ├── actions.py         # smart action library
│       ├── traversal.py       # recursive DFS traversal
│       └── replay.py          # YAML playbook engine
├── playbooks/                 # replay definitions
├── docs/index.md              # GitHub Pages dashboard (auto-updated)
├── RSS.md                     # incremental show feed (auto-updated)
├── monitor_history.json       # show monitor state (auto-updated)
├── archs_lgycp/archive.json   # article archive (auto-updated)
├── SITE_STRUCTURE.md          # API structure reference
└── .github/workflows/crawl.yml
```

## GitHub Actions

A single workflow runs both monitors every 12 hours (Beijing 08:00 / 20:00):

1. **Show monitor** — crawl all shows, check stock, update `RSS.md` + `docs/index.md`, email if new/restocked
2. **Article monitor** — search WeChat articles, archive new ones, email if new matches found

Supports manual trigger via **Actions → scheduled monitor → Run workflow**.

## Configuration

### Environment Variables

| Variable | Purpose | Where |
|----------|---------|-------|
| `LGPAC_TARGET_URL` | override target site URL | `vars` (optional) |
| `LGPAC_NOTIFY_EMAIL` | email recipient | `secrets` |
| `LGPAC_SMTP_USER` | SMTP sender address | `secrets` |
| `LGPAC_SMTP_PASS` | SMTP auth code | `secrets` |
| `LGPAC_SMTP_SERVER` | SMTP server (default: `smtp.qq.com`) | `secrets` |
| `LGPAC_SMTP_PORT` | SMTP port (default: `465`) | `secrets` |
| `LGPAC_WEBHOOK_URL` | webhook URL for instant notifications | `secrets` (optional) |

All sensitive values are stored as GitHub Secrets — never in code or logs.

## License

MIT — see [DISCLAIMER](#lgpac) above.
