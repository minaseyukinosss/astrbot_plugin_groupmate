# Groupmate WebUI 控制中心设计

日期：2026-07-22

状态：设计定稿，等待实现计划

## 目标

为 Groupmate 增加一个 AstrBot Plugin Pages 页面，让管理员可以在 AstrBot WebUI 内完成两件事：

1. 快速确认插件当前是否正常运行。
2. 连续审阅并标注影子决策，形成后续评测数据。

首页采用“概览优先”的结构，但必须保留一个可见的待处理入口，避免页面退化为只展示数字的监控面板。

## 已确认的设计决策

- 使用 AstrBot 原生 Plugin Pages，不单独启动 FastAPI、Flask 或前端开发服务器。
- 页面目录为 `pages/control-center/`，AstrBot 4.24+ 可用，保留现有 `>=4.24,<5` 兼容范围。
- 页面默认进入“概览”，完整连续标注流程进入“审阅”。
- 配置继续由 `_conf_schema.json` 和 AstrBot 自带配置页负责；本页面不实现第二套配置编辑器。
- 首版不做实时 SSE、复杂趋势图、群级策略编辑或消息重新发送。
- 页面不显示 QQ 号、群号、HMAC、数据库路径或未脱敏原始事件。

## 信息架构

### 概览首页

页面打开后按以下顺序显示：

1. **运行状态条**
   - 运行中、已暂停、影子模式等状态。
   - 已初始化群数量。
   - 最近一次数据更新时间。
   - 暂停/恢复操作；操作结果明确说明这是运行时状态，插件重载后是否恢复由现有配置决定。

2. **待处理入口**
   - 未标注影子决策数量。
   - “开始审阅”主操作，进入审阅页并默认筛选未标注。
   - 当没有待标注记录时显示空状态和隐私设置提示，而不是空白区域。

3. **最近待处理决策**
   - 默认显示最近三条。
   - 每条显示时间、动作（回复/沉默/旁路）、置信度、原因码和是否会被限流。
   - 如果 `shadow_store_message_text` 未开启，正文位置显示“未保存文本”；不尝试读取原始消息。
   - 每条提供进入审阅详情的操作。

4. **运行摘要**
   - 回复、沉默、旁路的数量分布。
   - 原因码的前几项。
   - 不把样本不足的数据渲染成百分比；需要人工标注样本的指标显示“样本不足”。

### 审阅页

审阅页围绕连续标注设计，而不是围绕详情浏览设计：

- 顶部筛选：未标注/全部、动作、时间范围；首版筛选值使用有限枚举，不接受任意 SQL 或路径输入。
- 主列表：按创建时间倒序，支持有限页大小和游标式翻页。
- 详情区域：展示脱敏上下文（仅在配置允许时）、动作、置信度、原因码、模型标识、延迟、限流判断和原始标签状态。
- 快捷标注：必须回复、可以回复、必须沉默、跳过。
- 标注成功后保留当前筛选并自动定位下一条未标注记录，失败时保留当前项并展示可读错误。
- 原始预测不可被人工标签覆盖；页面同时显示“模型判断”和“人工标签”。

### 数据入口

首版提供一个低强调的“导出已标注评测集”操作，复用现有 `export_labeled_shadow_dataset()`。导出的文件通过 Plugin Page 的 `download()` 下载，页面不暴露服务器文件路径。

“分析”独立页面暂不实现。等有足够标注数据并补齐时间聚合接口后再加入，避免首版产生没有统计意义的图表。

## 页面结构

```text
pages/control-center/
├── index.html
├── app.js
└── style.css
```

页面使用原生 HTML/CSS/JavaScript，避免新增 Node 构建链和运行时依赖。内部导航使用 hash 状态或页面内分区，不依赖 history fallback。所有静态资源使用相对路径，由 AstrBot 重写 asset token。

视觉方向是 AstrBot 管理面板内的克制型工具界面：单一强调色只用于当前状态、主要动作和标签结果；使用清晰的表格/列表分隔；移动宽度下导航和列表纵向堆叠；亮暗主题通过 AstrBot 注入的主题属性适配。状态、标签和动作同时使用文字，不依赖颜色单独传达含义。

## 后端边界

在插件初始化时通过 `context.register_web_api()` 注册页面 API。建议将请求解析、输入校验和响应序列化放在一个独立的 Web 适配模块中，继续调用 `AstrBotBridge` 和内存仓储，不让页面代码直接操作 SQLite。

路由统一使用插件名前缀；页面端只传递插件内相对 endpoint。

### 概览

`GET /astrbot_plugin_groupmate/dashboard/overview`

返回页面需要的聚合数据：

- `runtime`: `paused`、`shadow_mode`、`initialized_group_count`。
- `pending_count`。
- `recent`: 最近三条已脱敏决策。
- `actions`、`labels`、`reasons` 的计数分布。
- `data_policy`: 是否保存脱敏正文、影子记录保留天数、是否存在样本不足提示。

响应序列化时排除原始 `group_id`、`group_hash`、sender 标识和 `context_json`；运行态群快照只返回数量和必要的非身份状态。

### 决策列表

`GET /astrbot_plugin_groupmate/shadow/decisions`

支持：

- `label=unlabeled|must_respond|may_respond|must_silence|skipped|all`
- `action=respond|ignore|bypass|all`
- `limit`，服务端限制在合理范围内。
- `cursor`，由服务端生成，不能由客户端拼接 SQL。

响应包含 `items`、`next_cursor` 和 `has_more`。每项只返回页面需要的稳定字段，并返回用于标注的不可推导 `decision_id`；不返回数据库自增 ID 或身份哈希。

### 标注

`POST /astrbot_plugin_groupmate/shadow/decisions/<decision_id>/label`

请求体：

```json
{"label": "must_respond"}
```

服务端只接受现有四个标签；不存在的决策返回 404，非法标签返回 400。重复提交同一标签应视为幂等成功；提交不同标签时返回更新后的状态，不修改原始预测字段。

### 运行控制

- `POST /astrbot_plugin_groupmate/runtime/pause`
- `POST /astrbot_plugin_groupmate/runtime/resume`

这两个接口只修改当前插件实例的 `paused` 状态，不写入配置文件。页面需在响应中标识这是运行时切换。

### 导出

`GET /astrbot_plugin_groupmate/shadow/export`

复用现有导出逻辑生成或定位安全的数据文件，通过 `file_response()`（或 4.24 兼容的响应方式）返回下载。导出前再次检查只包含已标注、已脱敏的记录。

## AstrBot 兼容策略

- 页面能力使用 4.24 已存在的 `ready()`、`apiGet()`、`apiPost()` 和 `download()`。
- 后端优先使用新版本 `astrbot.api.web` 的请求/响应 helper；若目标运行环境没有该模块，则回退到 AstrBot 4.24 可用的 Quart 请求和响应对象。
- 不依赖 4.26 才出现的主题监听、插件国际化扩展或新的 OpenAPI 路由。
- 页面不直接读取 Cookie、LocalStorage 或父页面 DOM；所有请求都走 `window.AstrBotPluginPage` bridge。

## 数据流

```text
AstrBot 页面 iframe
        │  ready / apiGet / apiPost / download
        ▼
Plugin Page bridge
        ▼
AstrBot register_web_api 路由
        ▼
Web 适配模块：校验、脱敏、分页、错误映射
        ▼
AstrBotBridge / MemoryStore / export_labeled_shadow_dataset
```

页面初次加载只请求一次概览；进入审阅页时请求列表。标注成功后局部更新当前项、待标注计数和最近列表，不强制整页刷新。首版采用手动刷新按钮和页面进入时刷新，不建立长连接。

## 错误、空状态和隐私

- 插件未初始化：显示“插件正在初始化”，不显示 Python 异常堆栈。
- 读取失败：保留上一份已加载数据，显示重试操作。
- 暂停状态：状态条明确指出不会继续观察和自主回复。
- 无数据：解释影子模式和保留策略，不暗示系统出错。
- 未开启正文保存：所有正文位置统一显示“未保存文本”。
- 导出无可用记录：返回可读错误，不生成空文件。
- 所有服务端输入都做类型、范围和枚举校验；文件下载路径由服务端生成，禁止客户端指定路径。

## 测试要求

### 后端单元测试

- 概览序列化不泄露群号、群哈希、sender 标识和原始 JSON。
- 未开启正文保存时，列表和详情均返回脱敏占位文本。
- 标签枚举、重复提交、不存在 ID 和非法分页参数的行为稳定。
- pause/resume 只改变运行时状态，不调用配置保存。
- 导出接口只允许已标注记录，并复用现有脱敏导出逻辑。

### 页面与集成测试

- 静态资源路径在 Plugin Page iframe 中可加载。
- 页面能够完成概览加载、进入审阅、标注一条记录、刷新计数的闭环。
- 亮色/暗色主题下无文字溢出或不可读状态。
- 320px 以上宽度下导航、筛选、列表和操作不重叠。
- 页面 API 错误会显示可恢复的提示，不出现未捕获 Promise 错误。

现有领域测试继续运行；页面相关测试不依赖真实 AstrBot、NapCat 或 QQ 连接。

## 非目标

- 不实现第二套插件配置页面。
- 不在页面中展示或编辑模型密钥。
- 不允许页面代替 AstrBot WebUI 管理其他插件或平台。
- 不提供消息重发、删除群消息或主动向群发送消息的能力。
- 不在首版引入 React/Vue 构建链、独立服务端口或外部图表服务。

## 完成标准

当管理员从 AstrBot 插件详情页打开 Groupmate Page 时，可以先确认运行状态，再从首页进入待标注队列；可以在不暴露身份信息的前提下完成影子决策标注并下载已标注评测集；任何运行态、数据不足或权限错误都有明确反馈；现有 `>=4.24,<5` 支持范围不被无必要地抬高。
