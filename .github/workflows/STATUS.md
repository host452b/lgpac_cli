# Workflow Status Registry

> Last updated: 2026-04-17

## Active Workflows

| Workflow | File | Schedule | Status | Description |
|----------|------|----------|--------|-------------|
| **scheduled monitor** | `crawl.yml` | `0 0,12 * * *` (UTC 00:00 + 12:00) | **ACTIVE** | Ticket monitor + weixin article scan. Commits `RSS.md docs_lgpac/ docs_lgycp/ monitor_history.json archs_lgycp/` |
| **hn daily top-10** | `hn-daily.yml` | `0 20 * * *` + 0-4h random jitter | **ACTIVE** | HN top-10 via Firebase API (fallback: Algolia → hnrss → archive). Commits `archs_hn/ docs_hn/` |

## Disabled Workflows

| Workflow | File | Previous Schedule | Status | Reason | Disabled Date |
|----------|------|-------------------|--------|--------|---------------|
| **rss email digest** | `rss-email-daily.yml` | `0 2 * * *` (UTC 02:00) | **DISABLED** | Replaced by `hn-daily.yml` for HN tracking. Substack feeds no longer needed. Manual dispatch still available. | 2026-04-17 |

## Manual-Only Workflows

| Workflow | File | Status | Description |
|----------|------|--------|-------------|
| **xbirds daily digest** | `xbirds-daily.yml` | **MANUAL** | X/Twitter post tracker. Run via `workflow_dispatch` only. Default lookback 24h. |

---

## Changelog

### 2026-04-17
- **Added** `hn-daily.yml` — daily HN + zeli.app top-10 with 4-layer fallback
- **Disabled** `rss-email-daily.yml` cron — Substack/RSS digest stopped, replaced by HN tracker
- No changes to `crawl.yml` or `xbirds-daily.yml`

### 2026-04-11 (initial)
- `crawl.yml` — ticket monitor + lgycp every 12h
- `rss-email-daily.yml` — RSS digest daily at UTC 02:00
- `xbirds-daily.yml` — manual-only X/Twitter tracker
