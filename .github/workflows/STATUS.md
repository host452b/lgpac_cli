# Workflow Status Registry / 工作流状态总览

> Last updated / 最后更新: 2026-04-17

## Active / 运行中

| Workflow 工作流 | File 文件 | Schedule 调度 | Status 状态 | Description 说明 |
|----------------|-----------|---------------|-------------|-----------------|
| **scheduled monitor** 票务+微信监控 | `crawl.yml` | `0 0,12 * * *` (UTC 00:00 + 12:00 = 北京 08:00 + 20:00) | **ACTIVE** 运行中 | 票务监控（≤120 元）+ 微信公众号文章扫描。提交 `RSS.md docs_lgpac/ docs_lgycp/ monitor_history.json archs_lgycp/` |
| **hn daily top-10** HN 每日热榜 | `hn-daily.yml` | `0 20 * * *` + 随机 0-4h 延迟（实际 UTC 18:00-23:59） | **ACTIVE** 运行中 | HN Top 10，4 层 fallback：Firebase API → Algolia → hnrss.org → 上次存档。提交 `archs_hn/ docs_hn/` |

## Disabled / 已停用

| Workflow 工作流 | File 文件 | Previous Schedule 原调度 | Status 状态 | Reason 原因 | Date 日期 |
|----------------|-----------|------------------------|-------------|-------------|-----------|
| **rss email digest** RSS 邮件摘要 | `rss-email-daily.yml` | `0 2 * * *` (UTC 02:00 = 北京 10:00) | **DISABLED** 已停用 | 被 `hn-daily.yml` 替代。Substack/RSS 订阅源不再需要。手动触发 (`workflow_dispatch`) 仍可用。 | 2026-04-17 |

## Manual-Only / 仅手动触发

| Workflow 工作流 | File 文件 | Status 状态 | Description 说明 |
|----------------|-----------|-------------|-----------------|
| **xbirds daily digest** X/Twitter 追踪 | `xbirds-daily.yml` | **MANUAL** 手动 | X/Twitter 发帖追踪器。仅通过 `workflow_dispatch` 手动运行，默认回溯 24h。 |

---

## Changelog / 变更记录

### 2026-04-17
- **新增** `hn-daily.yml` — HN + zeli.app 每日 Top 10，含 4 层 fallback 保证可用性
- **停用** `rss-email-daily.yml` 定时任务 — Substack/RSS 摘要停止，由 HN tracker 替代
- `crawl.yml` 和 `xbirds-daily.yml` 无变更

### 2026-04-11 (初始)
- `crawl.yml` — 票务监控 + 微信文章扫描，每 12 小时执行
- `rss-email-daily.yml` — RSS 邮件摘要，每天 UTC 02:00
- `xbirds-daily.yml` — X/Twitter 追踪，仅手动触发

---

## 安全审计 / Security Audit (2026-04-17)

| Check 检查项 | Result 结果 |
|-------------|-------------|
| 敏感文件 (.env, .pem, id_rsa) | PASS — 无 |
| 硬编码密码/密钥/token | PASS — SMTP 凭证全部通过 `${{ secrets.* }}` 引用 |
| 个人邮箱泄露 | PASS — 无 |
| SSH/TLS 私钥 | PASS — 无 |
| AWS/GCP API Key | PASS — 无 |
| Git 作者身份 | PASS — 仅 `joe0731 <noreply>` + `github-actions[bot]` |
