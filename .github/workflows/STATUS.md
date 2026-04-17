# Workflow Status Registry

> Last updated: 2026-04-17

## Active

| Workflow | File | Schedule | Status | Description |
|----------|------|----------|--------|-------------|
| **scheduled monitor** | `crawl.yml` | `0 0,12 * * *` (UTC 00:00 + 12:00) | **ACTIVE** | Ticket monitor (<=120 CNY) + weixin article scan. Commits `RSS.md docs_lgpac/ docs_lgycp/ monitor_history.json archs_lgycp/` |
| **hn daily top-10** | `hn-daily.yml` | `0 20 * * *` + random 0-4h jitter (effective UTC 18:00-23:59) | **ACTIVE** | HN Top 10 with 4-layer fallback: Firebase API -> Algolia -> hnrss.org -> archive. Commits `archs_hn/ docs_hn/` |

## Disabled

| Workflow | File | Previous Schedule | Status | Reason | Date |
|----------|------|-------------------|--------|--------|------|
| **rss email digest** | `rss-email-daily.yml` | `0 2 * * *` (UTC 02:00) | **DISABLED** | Replaced by `hn-daily.yml`. Substack/RSS feeds no longer needed. Manual dispatch still available. | 2026-04-17 |

## Manual-Only

| Workflow | File | Status | Description |
|----------|------|--------|-------------|
| **xbirds daily digest** | `xbirds-daily.yml` | **MANUAL** | X/Twitter post tracker. Run via `workflow_dispatch` only. Default lookback 24h. |

---

## Changelog

### 2026-04-17
- **Added** `hn-daily.yml` — daily HN + zeli.app top-10 with 4-layer fallback
- **Disabled** `rss-email-daily.yml` cron — replaced by HN tracker
- No changes to `crawl.yml` or `xbirds-daily.yml`

### 2026-04-11 (initial)
- `crawl.yml` — ticket monitor + weixin scan every 12h
- `rss-email-daily.yml` — RSS email digest daily at UTC 02:00
- `xbirds-daily.yml` — X/Twitter tracker, manual-only

---

## Security Audit (2026-04-17)

| Check | Result |
|-------|--------|
| Sensitive files (.env, .pem, id_rsa) | PASS |
| Hardcoded passwords/keys/tokens | PASS — SMTP via `${{ secrets.* }}` only |
| Personal email addresses | PASS |
| SSH/TLS private keys | PASS |
| AWS/GCP API keys | PASS |
| Git author identity | PASS — `joe0731 <noreply>` + `github-actions[bot]` only |
