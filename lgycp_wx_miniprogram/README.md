# 临港少年宫小程序新课程订阅

这是一个独立、只读的课程监控器。它每天北京时间 11:05 请求一次小程序课程列表接口，使用接口返回的上架/发布时间筛选最近 7 天课程，并只向尚未通知过的课程发送一封汇总邮件。

## 行为边界

- 第一次成功运行只建立基线，不发送存量课程。
- 7 天窗口是闭区间：`运行时间 - 7 天 <= 上架时间 <= 运行时间`。
- 仅使用接口上架/发布时间；缺失或非法时间不会用本地首次发现时间代替。
- 接口没有报名开始或截止字段，因此不保存、也不推测“报名时间”。
- 课程图片、图片 URL、介绍正文和购买须知不进入归档、邮件或诊断文件。
- 不自动报名，不提交个人信息，不绕过验证码、签名或其他访问控制。
- 接口不能由普通 HTTP 客户端稳定重放时停止，不切换到 RPA。

## 已验证的课程接口

2026-07-07 已从“临港青少年活动中心”小程序确认并以普通、无认证的 HTTP GET 重放：

- URL：`https://lg-venue.xports.cn/aisports-api/api/training/queryTrainings0103`
- 固定参数：`channelId=11`、`centerId=32057878`、`pageNo=1`、`pageSize=999`
- 课程数组：`pageInfo.list`
- 课程 ID：`courseId`
- 课程名称：`courseName`
- 小程序上架/创建时间：`createTime`
- 价格：优先 `subjectPrice`，缺失时使用 `price`；接口单位按分转换为两位人民币金额
- 类型：`courseTypeName`
- 课程周期：`startDate`、`endDate`

接口当前返回 52 门课程且 `pageInfo.total` 与列表长度一致。程序会检查 `error` 和分页完整性；如果未来课程超过当前单页或响应结构变化，本次运行失败并保留旧归档，不会静默漏报。

仓库不保存 Token、Cookie、原始 cURL、HAR、完整小程序包或完整接口响应。测试 fixture 仅保留一门公开课程的最小字段样本。

## 配置

GitHub Secrets：

| 名称 | 用途 |
|---|---|
| `LGPAC_NOTIFY_EMAIL` | 收件地址 |
| `LGPAC_SMTP_USER` | SMTP 发件账号 |
| `LGPAC_SMTP_PASS` | SMTP 授权码 |
| `LGPAC_SMTP_SERVER` | SMTP 服务器，空值时默认 `smtp.qq.com` |
| `LGPAC_SMTP_PORT` | SMTP SSL 端口，空值时默认 `465` |

接口高级覆盖项（通常无需设置）：

| 名称 | 用途 |
|---|---|
| `LGYCP_WX_API_URL` | 覆盖默认课程 URL |
| `LGYCP_WX_API_PARAMS_JSON` | 覆盖默认 query 参数 JSON object |
| `LGYCP_WX_API_METHOD` | 覆盖默认 `GET` |
| `LGYCP_WX_API_HEADERS_JSON` | 额外请求头；默认 `{}` |
| `LGYCP_WX_API_BODY_JSON` | JSON 请求体；默认无 body |
| `LGYCP_WX_ITEMS_PATH` 等字段变量 | 接口结构变化时临时覆盖默认字段路径 |

字段路径相对于单条课程 object；课程数组路径相对于整个响应。路径只支持字典 key 的点分访问，不执行表达式。

## 本地验证

在仓库根目录安装依赖并运行测试：

```bash
python -m pip install -r lgycp_wx_miniprogram/requirements.txt
python -m pytest lgycp_wx_miniprogram/tests -q
```

将真实配置放入当前 shell 环境后执行一次只读检查：

```bash
python -m lgycp_wx_miniprogram.main
```

第一次空归档成功运行会更新 `lgycp_wx_miniprogram/data/archive.json`，但不会发邮件。仓库已在 2026-07-07 建立 52 门课程的真实基线，因此正常部署后的运行属于后续运行。不要删除或手工重建这个文件，否则会改变基线语义。

## 归档与更新语义

归档使用 schema v2。课程 ID 作为 `courses` object 的 key，每条记录只保存：

- `course_name`、`price_yuan`、`course_type`
- 来自 `createTime` 的 `published_at`
- `course_start_date`、`course_end_date`
- 去重和审计必需的 `first_seen_at`、`last_seen_at`、`baseline`、`notified_at`

顶层 `last_success_at` 和 `last_run` 记录最近一次完整成功的课程总数、解析数、跳过数、新发现数、候选数、通知数、消失数和最早/最新发布时间。schema v1 会先在内存中迁移；只有本次完整成功后才原子保存为 v2。

后续运行遇到新课程 ID 时，只有 `运行时间 - 7 天 <= createTime <= 运行时间` 才发送邮件。新发现但已经超窗的课程只归档；同名但新 ID 仍视为新课程。本次完整列表里暂时消失的旧课程不会删除，也不会更新其 `last_seen_at`，Git 历史因此保留可审计状态。

## 自动运行

`.github/workflows/lgycp-wx-miniprogram-daily.yml` 只有一个 cron：`5 3 * * *`，即每天 UTC 03:05、北京时间 11:05；没有晚间健康检查或自动补跑。也可以从 GitHub Actions 使用 `workflow_dispatch` 手工触发。

工作流依次执行离线测试、课程检查和归档提交。只有监控成功且归档发生变化时才提交 `lgycp_wx_miniprogram/data/archive.json`。每次运行会写 GitHub Job Summary；失败时上传名为 `lgycp-wx-diagnostics-<run_id>` 的 artifact，包含脱敏诊断 JSON 和工作流日志，保留 30 天。

## 故障排查

- `configuration failed`：检查必需 Secret/Variable 是否为空，JSON 配置是否为 object。
- HTTP 401/403：公开接口开始要求认证或拒绝请求；停止运行并重新确认合法访问方式，不提交认证头到 Git。
- HTTP 408、425、429、5xx 或网络超时：总共最多请求三次；合法 `Retry-After` 最长等待 30 秒，仍失败时保留旧归档。
- `course list is empty or invalid`：响应结构、字段路径或分页规则可能变化；不要用空结果覆盖归档。
- `no valid courses in response`：课程缺少名称或可解析的接口上架时间。
- `email delivery failed`：课程不会标记为已通知，下次成功运行会重试。
- `archive save failed`：旧归档通过原子替换得到保护；检查磁盘权限或文件状态。

先在失败的 Action 页面下载 `lgycp-wx-diagnostics-<run_id>` artifact，查看 `failed_stage`、`safe_message` 和完整 traceback。修复接口契约或配置后使用 `workflow_dispatch` 人工重跑，不必等待第二天。

通知采用“不漏报优先”的 at-least-once 语义：只有 SMTP 成功后才在内存中写 `notified_at`。如果 SMTP 已成功，但随后归档保存或 Git push 最终失败，下一次运行可能重复通知；系统不会为避免重复而提前标记，从而造成静默漏报。

诊断只包含状态码、scheme/host/path、字段路径、key 名、课程数量和脱敏 traceback；不会写 URL query value、请求/响应 headers、body、Token、Cookie、SMTP 配置值、课程正文或个人信息。当前没有验证过语义等价的备用 API，失败时不会自动切换数据源。
