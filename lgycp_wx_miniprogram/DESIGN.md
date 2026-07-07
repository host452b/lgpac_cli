# 临港少年宫小程序新课程邮件订阅设计

状态：已确认

日期：2026-07-07

## 1. 目标

每天北京时间 11:05 请求一次临港少年宫微信小程序的课程列表接口，只处理接口所标记的最近 7 天内上架课程，并仅对尚未通知过的课程发送一封汇总邮件。

整个功能作为独立子项目放在 `lgycp_wx_miniprogram/` 中，不修改或依赖现有 `lgpac/`、`lgycp` 等业务模块。受 GitHub 平台目录约束，定时工作流文件是唯一例外，放在 `.github/workflows/`，其中只负责安装依赖并调用本子项目入口。

## 2. 不在范围内

- 不联系机构或供应商申请官方接口。
- 不使用 Appium、OCR、桌面微信或真机 RPA。
- 不监控公众号文章，不建立多来源降级链路。
- 不自动报名、占座或提交任何个人信息。
- 不绕过验证码、证书锁定、接口签名或其他访问控制。
- 不提供分钟级或实时通知。

## 3. 已验证前置条件

2026-07-07 已确认目标小程序 AppID 为 `wx30b66dcae36d63cd`，并从“课外教育 → 查看全部”的静态代码和真实只读请求验证课程接口：

- `GET https://lg-venue.xports.cn/aisports-api/api/training/queryTrainings0103`
- 不需要 Cookie、Token、登录态或客户端签名。
- 固定参数包含 `channelId=11`、`centerId=32057878`、`pageNo=1`、`pageSize=999`。
- 响应课程数组为 `pageInfo.list`，上架/创建时间字段为 `createTime`。
- 课程稳定 ID 为 `courseId`，名称为 `courseName`，场馆名称为 `centerName`。

当前返回 `pageInfo.total=52`，与列表长度一致。程序必须验证服务端 `error` 和分页完整性；不能完整获取所有课程时失败退出，不覆盖归档。

## 4. 目录边界

计划结构如下：

```text
lgycp_wx_miniprogram/
├── DESIGN.md
├── README.md
├── main.py
├── config.py
├── client.py
├── models.py
├── monitor.py
├── storage.py
├── notify.py
├── requirements.txt
├── data/
│   └── archive.json
└── tests/
    ├── fixtures/
    ├── test_client.py
    ├── test_monitor.py
    └── test_storage.py

.github/workflows/
└── lgycp-wx-miniprogram-daily.yml
```

模块职责：

- `config.py`：读取接口、SMTP、超时和时区配置，不保存秘密默认值。
- `client.py`：发送课程列表请求并把原始响应交给解析层。
- `models.py`：定义规范化课程结构。
- `monitor.py`：执行 7 天过滤、去重和通知候选计算。
- `storage.py`：原子读写课程归档状态。
- `notify.py`：生成并发送新课程汇总邮件。
- `main.py`：编排一次完整运行并提供进程退出码。

## 5. 数据模型

每门课程至少规范化为以下字段：

- `course_id`：优先使用接口的稳定课程 ID。
- `title`：课程名称。
- `published_at`：接口返回的课程上架或发布时间。
- `campus`、`term`、`schedule`：接口存在时保留，用于邮件展示和身份兜底。
- `price`、`remaining`、`detail_url`：接口存在时保留，不作为通知必需字段。

如果接口没有稳定课程 ID，则使用课程名称、校区、学期和上课时间的规范化组合生成 SHA-256 身份指纹。上架时间不参与身份指纹，避免同一课程时间字段格式变化后被误认为新课程。

归档状态区分“已发现”和“已通知”：

```json
{
  "schema_version": 1,
  "initialized_at": "ISO-8601 timestamp",
  "courses": {
    "course identity": {
      "published_at": "ISO-8601 timestamp",
      "first_seen_at": "ISO-8601 timestamp",
      "last_seen_at": "ISO-8601 timestamp",
      "baseline": true,
      "notified_at": "ISO-8601 timestamp or null"
    }
  }
}
```

`first_seen_at` 只用于审计，绝不参与“最近 7 天上架”判断。第一次成功运行发现的课程标记为 `baseline: true`；以后首次发现的课程标记为 `baseline: false`，从而明确区分“基线中已有但未发邮件”和“等待通知”的课程。

## 6. 时间规则

- 调度时间：每天北京时间 11:05。
- GitHub Actions 使用 UTC cron：`5 3 * * *`。
- 业务时区固定为 `Asia/Shanghai`。
- 过滤窗口为闭区间：`运行时间 - 7 天 <= published_at <= 运行时间`。
- 接口时间带有时区时，转换到 `Asia/Shanghai` 后比较。
- 接口时间没有时区时，按 `Asia/Shanghai` 解释。
- 缺少、格式错误或晚于运行时间的 `published_at` 课程不进入通知候选，并记录不含秘密信息的警告。
- 不允许用本地首次发现时间替代接口上架时间。

## 7. 单次运行数据流

1. 读取配置并校验必需环境变量。
2. 请求课程列表接口。
3. 校验响应为非空、结构可识别的课程集合。
4. 将原始课程映射为规范化课程模型。
5. 解析接口返回的 `published_at`，过滤最近 7 天内上架的课程。
6. 读取 `data/archive.json`，计算尚未成功通知的课程。
7. 将全部通知候选合并为一封 HTML 邮件。
8. 邮件发送成功后写入 `notified_at`；发送失败则保持为空，以便次日重试。
9. 更新 `last_seen_at`，使用临时文件加原子替换保存归档。

第一次成功运行只建立基线：记录当前接口返回的全部课程并标记为 `baseline: true`，不发送邮件。第二次及以后，只有 `baseline: false`、上架时间位于 7 天窗口内且 `notified_at` 为空的课程才发送邮件。

## 8. 邮件行为

邮件主题包含新增课程数量，例如：

```text
[临港少年宫] 3 门新课程
```

正文按课程逐项展示课程名称、上架时间，并在接口存在时展示校区、学期、上课时间、价格、剩余名额和详情链接。

同一次运行的全部新增课程只发送一封邮件。没有候选课程时不发送邮件。只有 SMTP 返回成功后，课程才标记为已通知。

## 9. 错误处理

- 网络超时、连接失败和 HTTP 5xx 最多进行两次有限重试。
- HTTP 4xx 不自动重试，避免持续使用失效或错误凭据。
- 401 或 403 以非零状态退出，日志提示检查 GitHub Secrets，但不输出令牌或完整请求头。
- 空响应、无法识别的响应结构或异常骤降为空课程集合时，本次运行失败且不覆盖旧归档。
- 单条课程字段异常只跳过该条并记录警告，其余有效课程继续处理。
- 邮件发送失败时不写入 `notified_at`，次日仍可重试。
- 归档写入采用原子替换，避免中断后留下半写 JSON。

## 10. 配置与安全

公开课程接口 URL、固定 query 参数和字段映射作为非敏感默认值保存在代码中，并允许通过环境变量覆盖。SMTP 凭据只通过 GitHub Secrets 或本地环境变量注入。仓库不提交 Cookie、Token、客户端静态密钥、完整抓包、小程序包或个人信息。

GitHub Actions 工作流只需要读取 Secrets、运行脚本，并在归档变化时提交 `lgycp_wx_miniprogram/data/archive.json`。其权限限制为完成该提交所需的最小范围。

## 11. 测试设计

测试使用脱敏的本地 JSON fixture，不访问真实小程序接口，也不发送真实邮件。至少覆盖：

- 正好 7 天前上架的课程被包含，超过 7 天的课程被排除。
- 未来时间、缺失时间和非法时间被排除并产生警告。
- 无时区时间按 `Asia/Shanghai` 解释。
- 第一次运行建立基线且不发送邮件。
- 第二次运行发现符合窗口的新课程并只发送一次。
- 新发现但已超过 7 天的课程不发送邮件。
- 同一课程再次出现不重复通知。
- SMTP 失败后不标记已通知，下一次运行能够重试。
- 请求失败、空响应或结构变化时保留旧归档。
- 缺少稳定 ID 时生成的身份指纹保持确定性。
- 日志不包含认证信息。

## 12. 验收标准

- 所有业务文件均位于 `lgycp_wx_miniprogram/`，只有薄工作流入口位于 `.github/workflows/`。
- 每天北京时间 11:05 自动执行一次。
- 7 天判断只使用接口返回的上架或发布时间。
- 首次运行不发送存量课程邮件。
- 后续符合窗口的新课程恰好通知一次。
- 邮件失败可在次日重试，抓取失败不会破坏历史状态。
- 凭据只存在于环境变量或 GitHub Secrets。
- 单元测试完全离线且覆盖时间边界、去重、失败重试和归档保护。
