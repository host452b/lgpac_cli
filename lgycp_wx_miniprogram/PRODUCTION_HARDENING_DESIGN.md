# 临港少年宫课程监控生产加固设计

状态：已选择方案 A，待用户审阅

日期：2026-07-07

## 1. 目标

在保持每天北京时间 11:05 单次调度、不增加当晚补跑的前提下，使课程监控具备长期可维护的状态归档和故障诊断能力：成功运行留下可读审计信息，失败运行留下完整但脱敏的 trace，修复后可通过 GitHub Actions `workflow_dispatch` 人工重跑。

本设计继续坚持“不漏报优先”的 at-least-once 语义。极端情况下，如果 SMTP 已成功但归档保存或 Git push 最终失败，下一次运行可能重复通知同一批课程；系统不能为了避免重复而提前标记成功，因为那会产生漏报风险。

## 2. 明确不做

- 不增加第二个定时任务或当晚健康补跑。
- 不引入 RPA、OCR、自动报名、占座或个人信息提交。
- 不自动切换未经验证的数据源或备用 API。
- 不把 HTTP 请求头、Cookie、Token、SMTP 授权码、完整响应正文或小程序包写入日志、归档、artifact 或 Git。
- 不承诺 SMTP 的 exactly-once；SMTP 没有可供本项目使用的幂等提交协议。

## 3. 当前业务状态机保持不变

### 3.1 空归档首次运行

1. 请求完整课程列表并验证 HTTP、JSON、服务端 `error`、分页完整性和课程必需字段。
2. 所有课程按 `courseId` 建立记录并标记 `baseline: true`。
3. 首次运行不发送任何存量课程邮件，即使某门课的 `createTime` 位于最近 7 天。
4. 原子保存归档。

项目已经在 2026-07-07 建立真实基线：52 门课程、52 条 `baseline: true`、0 条已通知。因此部署到 GitHub 后的首次运行在业务语义上属于后续运行。

### 3.2 后续运行

1. 每次仍请求完整课程列表；接口当前没有经过验证的服务端 `createTime` 过滤参数。
2. 对归档中不存在的新 `courseId` 建立 `baseline: false` 记录。
3. 仅当 `now - 7 days <= createTime <= now` 且 `notified_at` 为空时，课程进入通知候选。
4. 同次运行的全部候选合并为一封邮件。
5. SMTP 成功后写入 `notified_at`，再原子保存归档。
6. 已归档的相同 `courseId` 不重复通知；同名但新 `courseId` 视为新课程。
7. 新发现但 `createTime` 已超过 7 天的课程只归档，不通知。

### 3.3 失败语义

- API、JSON、契约或归档读取失败：不修改归档，进程非零退出。
- SMTP 失败：不标记通知成功，下一次运行继续重试。
- 归档保存失败：旧归档由原子替换保护，进程非零退出。
- Git push 失败：工作流保留现有三次 rebase/push 重试；最终失败时 Action 标红并保存工作流 trace。
- SMTP 成功但归档保存或 Git push 最终失败：允许下一次重复发送，不允许静默漏报。

## 4. 可读长期归档

归档升级为 schema version 2。每门课程只保存用户指定的业务字段，以及去重、重试和通知审计必需的系统字段：

```json
{
  "schema_version": 2,
  "initialized_at": "2026-07-07T13:44:30+08:00",
  "last_success_at": "2026-07-08T11:05:12+08:00",
  "last_run": {
    "run_id": "20260708T110500+0800",
    "started_at": "2026-07-08T11:05:00+08:00",
    "finished_at": "2026-07-08T11:05:12+08:00",
    "source_total": 53,
    "parsed_count": 53,
    "skipped_invalid_count": 0,
    "newly_seen_count": 1,
    "eligible_count": 1,
    "notified_count": 1,
    "missing_count": 0,
    "oldest_published_at": "2025-06-06T11:05:13+08:00",
    "newest_published_at": "2026-07-08T09:30:00+08:00"
  },
  "courses": {
    "10030001": {
      "course_name": "示例课程",
      "price_yuan": "640.00",
      "course_type": "无人机创客营",
      "published_at": "2026-07-08T09:30:00+08:00",
      "course_start_date": "2026-07-15",
      "course_end_date": "2026-08-15",
      "first_seen_at": "2026-07-08T11:05:00+08:00",
      "last_seen_at": "2026-07-08T11:05:00+08:00",
      "baseline": false,
      "notified_at": "2026-07-08T11:05:12+08:00"
    }
  }
}
```

规则：

- `course_name` 来自 `courseName`，`course_type` 来自 `courseTypeName`。
- `published_at` 来自小程序的 `createTime`，是最近 7 天判断的唯一业务时间。
- `course_start_date` 和 `course_end_date` 来自 `startDate`、`endDate`，明确表示课程周期，不称为报名时间。
- 接口没有报名开始或截止字段，因此不保存、不推测报名时间；未来接口明确提供后再通过 schema 迁移增加。
- 价格优先使用 `subjectPrice`，缺失时使用 `price`；接口金额按分解释并转换为固定两位小数的人民币字符串 `price_yuan`，避免浮点误差。
- 本次响应出现的课程更新 `last_seen_at` 和最新业务字段。
- 归档中存在、但本次完整响应未出现的课程不删除，保留原 `last_seen_at` 供 Git 历史审计。
- `last_success_at` 和 `last_run` 只在完整抓取、解析、通知和归档保存均成功后更新。
- 归档不保存场馆、报名信息、购买须知、介绍正文、图片 URL、图片内容、用户信息或完整原始课程 object。
- Git 历史本身提供每次成功归档的历史版本，不额外提交每日完整快照。

### 4.1 schema v1 到 v2 迁移

读取 v1 归档时执行确定性内存迁移：

- 保留全部课程 ID、`published_at`、`first_seen_at`、`last_seen_at`、`baseline`、`notified_at`。
- 新增 `course_name: ""`、`price_yuan: ""`、`course_type: ""`、`course_start_date: ""`、`course_end_date: ""`；下次看到该课程时从接口补齐。
- 顶层新增 `last_success_at: null` 和 `last_run: null`。
- 只有本次监控完整成功时才以 schema v2 原子保存；迁移失败时保留原文件。

## 5. 详细脱敏 trace

新增独立诊断模块，记录每次运行的阶段、计数、耗时和异常链。运行阶段固定为：

1. `configuration`
2. `http_fetch`
3. `json_decode`
4. `contract_validation`
5. `course_normalization`
6. `archive_load`
7. `candidate_selection`
8. `smtp_delivery`
9. `archive_save`
10. `git_commit_push`（由工作流记录）

失败诊断 JSON 至少包含：

```json
{
  "run_id": "20260708T110500+0800",
  "status": "failed",
  "failed_stage": "contract_validation",
  "started_at": "2026-07-08T11:05:00+08:00",
  "failed_at": "2026-07-08T11:05:07+08:00",
  "duration_ms": 7341,
  "exception_type": "CourseParseError",
  "safe_message": "course response is incomplete",
  "traceback": "完整 Python traceback",
  "http": {
    "method": "GET",
    "host": "lg-venue.xports.cn",
    "path": "/aisports-api/api/training/queryTrainings0103",
    "status_code": 200,
    "attempts": 1,
    "elapsed_ms": 4210
  },
  "contract": {
    "response_type": "object",
    "top_level_keys": ["error", "message", "pageInfo", "sysdate"],
    "source_total": 53,
    "list_length": 12,
    "items_path": "pageInfo.list",
    "title_path": "courseName",
    "published_path": "createTime"
  },
  "runtime": {
    "git_sha": "GitHub SHA",
    "python_version": "3.11.x",
    "requests_version": "2.x",
    "github_run_id": "GitHub run ID"
  }
}
```

脱敏规则：

- URL 仅保留 scheme、host 和 path，不记录 query value。
- 不记录 request/response headers、body、Cookie、Token、SMTP 用户名或授权码。
- `safe_message` 只使用项目定义的固定错误消息，不直接写服务端正文或异常对象 repr。
- traceback 保留文件、函数、行号和异常链；业务异常必须先转换为固定安全消息。
- `top_level_keys` 只记录 key 名，绝不记录 value。
- 课程诊断只记录数量和字段路径，不记录完整课程内容。

## 6. GitHub Actions 可观测性

工作流仍只有一个定时 cron：北京时间 11:05，并保留 `workflow_dispatch`。

每次运行：

- Python 进程把结构化诊断写到 `${RUNNER_TEMP}/lgycp-wx-diagnostics.json`。
- GitHub Job Summary 展示状态、耗时、课程总数、新发现数、候选数、通知数和归档课程总数。
- 失败时使用 `actions/upload-artifact@v4` 上传诊断 JSON 和工作流阶段日志，artifact 名包含 GitHub run ID，保留 30 天。
- artifact 上传步骤使用 `if: failure()`，不因前序步骤失败而跳过。
- 成功运行不上传 artifact，避免长期存储噪声；成功摘要和 Git 提交已经提供审计证据。
- Git push 阶段把三次 push/rebase 的退出码和标准错误追加到独立日志，但不打印环境变量。

## 7. HTTP 瞬时错误处理

保留总计三次请求，并增强为：

- 连接错误、超时、HTTP 408、425、429 和 5xx 可以重试。
- HTTP 429/503 存在合法 `Retry-After` 秒数时优先使用，但单次等待最多 30 秒。
- 其他情况使用带确定上限的小幅指数退避和 jitter，避免所有任务同时重试。
- 其余 4xx 不重试，立即失败并生成 trace。
- 三次失败后保留旧归档，Action 标红；不自动切换备用 URL。

## 8. 邮件发送一致性

课程监控复用仓库现有 `lgpac.notify.send_email()`，统一读取：

- `LGPAC_NOTIFY_EMAIL`
- `LGPAC_SMTP_USER`
- `LGPAC_SMTP_PASS`
- `LGPAC_SMTP_SERVER`
- `LGPAC_SMTP_PORT`

课程模块只负责生成主题和安全转义后的 HTML。这样 SMTP 默认值、SSL、登录和超时只有一个实现，不会与原邮件任务逐渐漂移。

为获得完整 SMTP traceback，共享函数增加向后兼容的关键字参数 `raise_on_error=False`：

- 现有任务不传参数，继续在失败时记录警告并返回 `False`，行为不变。
- 课程监控传入 `raise_on_error=True`；SMTP 异常被包装为固定安全消息的 `EmailDeliveryError`，并使用 Python exception chaining 保留原始 traceback。
- 诊断文件记录异常类型、固定安全消息和 traceback，不记录 SMTP 用户名、授权码或邮件正文。

邮件增加确定性的 `Message-ID` 需要修改共享发送接口，因此本轮不加入；它不能保证 SMTP 去重，且会扩大现有任务的行为变化。本轮明确维持 at-least-once，并通过归档和 push 重试降低重复概率。

## 9. 数据源 fallback 原则

生产 fallback 采用“配置覆盖 + 失败关闭”：

- URL、query 参数和字段路径继续允许环境变量覆盖。
- 只有备用接口经过人工只读验证，确认课程集合、`courseId` 和 `createTime` 语义等价后，才能加入代码中的自动 fallback 列表。
- 当前没有第二个完成等价验证的生产接口，因此不实现自动 API fallback。
- 修复接口契约后通过 `workflow_dispatch` 人工重跑；失败期间旧归档保持不变，新课程仍可在 7 天窗口内补发。

## 10. 测试与验收

离线测试必须新增：

- v1 归档无损迁移到 v2。
- 成功运行写入 `last_success_at`、`last_run`、课程名、价格、类型、发布时间和课程周期。
- 价格优先读取 `subjectPrice`、回退到 `price`，并从分精确转换为两位人民币字符串。
- 接口没有报名时间时不生成或推测报名时间字段。
- 课程消失时保留记录和原 `last_seen_at`，不删除历史。
- 归档、诊断和 Git diff 中不存在图片 URL 或图片内容。
- 新课程、超窗课程、重复 ID、SMTP 失败和保存失败保持现有语义。
- 每个失败阶段生成包含 traceback 的诊断文件。
- 诊断文件不包含请求头、认证词值、SMTP 配置或响应正文。
- 408、425、429、5xx、`Retry-After` 和最大三次重试。
- 工作流只有 `5 3 * * *` 一个 cron，并保留手工触发。
- 工作流失败时上传 30 天 artifact，成功时不上传。
- 新邮件路径调用共享 `lgpac.notify.send_email(..., raise_on_error=True)`。
- 共享邮件函数默认调用方式保持原有布尔返回语义，详细模式保留 SMTP exception chain。

最终验收：

- 全部测试、Ruff、格式、编译、YAML 和 `git diff --check` 通过。
- 公开接口只读 smoke test 返回完整、可解析课程列表。
- 使用临时归档模拟首次和后续运行，不发送真实邮件。
- 使用受控 SMTP mock 验证失败重试，不在验证期间发送测试邮件。
- 对 Git diff 和诊断样本执行高熵凭据及敏感字段扫描，零真实凭据命中。

## 11. 运维使用方式

- 正常情况：每天 11:05 自动执行并提交可读归档。
- 失败情况：GitHub Action 标红；从失败 run 下载 `lgycp-wx-diagnostics-<run_id>` artifact，按 `failed_stage`、traceback 和契约摘要修复。
- 修复后：使用 `workflow_dispatch` 手工重跑；不等待下一天，也不增加自动补跑计划。
- 排查接口变化：先以普通只读 HTTP 客户端验证，再更新默认配置或 GitHub 环境覆盖；不把抓包或认证材料提交到 Git。
