# lgpac & lgycp

Two independent monitors running on a shared infrastructure. Built for learning and research purposes.

> **DISCLAIMER / 免责声明**
>
> This project is for **educational and research purposes only**. It is not intended for commercial use, unauthorized data collection, or any activity that violates applicable laws or the target website's terms of service. The authors assume no liability for any misuse. Use at your own risk and responsibility.
>

---

## lgpac — performing arts ticket monitor

Monitors a performing arts venue's ticketing platform for show listings, real-time ticket stock, and price changes.

### What it does

- Crawls all shows via public API (list → detail → sessions → seat plans)
- Checks real-time stock using `canBuyCount` from the dynamic API
- Tracks price changes, new shows, and sold-out events across runs
- Generates a GitHub Pages dashboard (`docs/index.md`) with inline ticket stock icons
- Sends email when a **new show** appears or a **sold-out cheapest tier** comes back in stock

### Commands

```bash
# ticket monitor (the main command for scheduled use)
python lgpac_cli.py monitor --price 120 --rss --page --notify --email

# raw data crawl
python lgpac_cli.py crawl              # full crawl
python lgpac_cli.py crawl -q           # quick: list only
python lgpac_cli.py crawl --rss        # also update RSS.md

# site info
python lgpac_cli.py info

# recursive site traversal (needs playwright)
python lgpac_cli.py traverse --archs --depth 2 -d

# YAML playbook execution (needs playwright)
python lgpac_cli.py replay playbooks/check_show.yaml -d

# local periodic crawling
python lgpac_cli.py schedule -i 60
```

### Output

| File | Content | Updated by |
|------|---------|-----------|
| `docs_lgpac/index.md` | dashboard with ticket listing, stock, prices | `monitor --page` |
| `RSS.md` | incremental feed with diff and affordable ticket table | `monitor --rss` |
| `monitor_history.json` | show state tracking (first_seen, had_stock) | `monitor` |
| `data/latest/shows.json` | full show data (git-ignored, local only) | `crawl` / `monitor` |

---

## lgycp — WeChat article monitor

Monitors WeChat public account articles for youth activity center enrollment notices, class schedules, and event announcements.

### What it does

- Searches WeChat articles via sogou, with automatic fallback to baidu and bing
- Filters by keywords: 报名, 通知, 开课, 招生, 招募, 儿童剧, 舞蹈, 体能, 英语, etc.
- Archives all seen titles — only new (unseen) articles trigger notifications
- Sends email with clickable article links when new matches are found

### Commands

```bash
# check for new articles
python lgpac_cli.py lgycp

# with email notification
python lgpac_cli.py lgycp --notify

# custom search query
python lgpac_cli.py lgycp -q "临港少年宫" --notify
```

### Output

| File | Content | Updated by |
|------|---------|-----------|
| `docs_lgycp/index.md` | article listing page (newest first) | `lgycp --page` |
| `archs_lgycp/archive.json` | all seen article titles + URLs + timestamps | `lgycp` |

### Fallback chain

```
sogou weixin search (primary)
  ↓ fails or returns 0 results
baidu site:mp.weixin.qq.com (fallback 1)
  ↓ fails or returns 0 results
bing site:mp.weixin.qq.com (fallback 2)
```

All providers return the same normalized format — downstream logic is unaffected.

---

## Shared Infrastructure

### Requirements

```bash
pip install requests typer rich pyyaml

# optional: for traverse/replay commands
pip install playwright && python -m playwright install chromium
```

### GitHub Actions

A single workflow (`.github/workflows/crawl.yml`) runs both monitors every 12 hours (Beijing 08:00 / 20:00):

1. `python lgpac_cli.py monitor --price 120 --rss --page --notify --email`
2. `python lgpac_cli.py lgycp --notify`
3. Commits `RSS.md`, `docs/`, `monitor_history.json`, `archs_lgycp/` back to repo

Also supports manual trigger: **Actions → scheduled monitor → Run workflow**.

### Configuration (GitHub Secrets)

| Secret | Purpose |
|--------|---------|
| `LGPAC_NOTIFY_EMAIL` | email recipient |
| `LGPAC_SMTP_USER` | SMTP sender address |
| `LGPAC_SMTP_PASS` | SMTP authorization code |
| `LGPAC_SMTP_SERVER` | SMTP server (default: `smtp.qq.com`) |
| `LGPAC_SMTP_PORT` | SMTP port (default: `465`) |
| `LGPAC_WEBHOOK_URL` | webhook URL (optional) |
| `LGPAC_TARGET_URL` | override target site URL (optional, set as `vars`) |

### Project Structure

```
├── lgpac_cli.py                # entry point
├── pyproject.toml
├── lgpac/
│   ├── cli.py                  # all CLI commands
│   ├── config.py               # site config & API routing
│   ├── client.py               # HTTP client (retry, rate-limit)
│   ├── api.py                  # ticketing API wrappers
│   ├── models.py               # data models (Show, Session, SeatPlan)
│   ├── monitor.py              # ticket monitor + email (lgpac)
│   ├── lgycp.py                # article monitor + email (lgycp)
│   ├── page.py                 # docs/index.md generator
│   ├── rss.py                  # RSS.md incremental feed
│   ├── spider.py               # crawl orchestration
│   ├── storage.py              # JSON persistence + diff
│   ├── scheduler.py            # interval scheduler
│   └── browser/
│       ├── engine.py           # Playwright wrapper
│       ├── actions.py          # smart action library
│       ├── traversal.py        # recursive DFS
│       └── replay.py           # YAML playbook engine
├── playbooks/                  # replay definitions
├── docs_lgpac/index.md          # ticket monitor page (auto-updated)
├── docs_lgycp/index.md          # article monitor page (auto-updated)
├── RSS.md                      # show feed (auto-updated)
├── monitor_history.json        # show state (auto-updated)
├── archs_lgycp/archive.json    # article archive (auto-updated)
├── SITE_STRUCTURE.md           # API reference
└── .github/workflows/crawl.yml
```

## License

MIT — see [DISCLAIMER](#lgpac--lgycp) above.
