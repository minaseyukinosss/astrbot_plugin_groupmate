# Runtime Strategy And Cognition Diagnostics Design

## Goal

让管理员无需理解内部英文枚举，就能从运行中心确认一条消息走了“直接互动、对话延续、普通群聊观察”中的哪条策略、正式模式是否会回复，以及未回复是否由认知超时导致。同时消除 Groupmate 固定 10 秒模型截止时间造成的误判，并保留保守失败策略。

## Confirmed Problems

- 后端追踪已经写入 `participation_lane`、`would_reply`、`candidate_count`、`candidate_source` 和 `participation_diagnostics`，详情面板没有读取这些字段。
- `DEGRADED`、`level0.rules`、`scene_interpreter`、`participation_assessor` 和 `worker_timeout` 直接以内部英文显示。
- “异常”筛选只检查交付状态，因此认知超时仍被归入“未参与”。
- Groupmate 在 `CognitionService` 内使用固定 10 秒 `asyncio.wait_for` 包裹 AstrBot `llm_generate`。截图中两个 Worker 均精确为 10000 ms，说明是 Groupmate 截止时间主动取消调用，而不是页面计时错误。
- 当前追踪没有保存 Provider 异常类型；旧 SHADOW 数据无法进一步区分网络、限流或 Provider 实现错误。

## Design

### Plain-language trace presentation

新增统一的前端追踪呈现函数，将策略、状态、候选来源和诊断码转换为中文。消息列表直接显示策略通道和“正式运行会回复/不会回复”；详情页增加“策略判断”，展示策略通道、判断结果、候选数量与来源。认知模块卡片显示中文名称、中文状态和可行动说明，原始诊断码移入“技术信息”。

### Runtime-level visibility

顶部运行概览将“已发送”之外增加“认知异常”计数。筛选改为“会回复、继续观察、外部能力、异常”；异常同时覆盖交付失败和认知降级，避免超时被正常观察结果掩盖。

### Timeout correction and diagnostics

新增 `cognition_timeout_seconds` 插件设置，默认 20 秒并传入 `CognitionBudget`，替代固定 10 秒。认知仍保持并行和 fail-closed：超时只会导致普通群聊继续观察，不会绕过治理。Provider 在截止时间前抛出的异常只记录异常类型，不记录 URL、密钥、Prompt 或响应正文。

## Scope

本次不改参与策略阈值、不合并认知 Worker、不增加重试，也不扩大页面模块。只修复已确认的数据契约、文案、筛选和超时配置问题。

## Verification

只运行相关的 settings、trace、cognition 契约测试和一次前端静态/构建检查，不运行完整压力或全量测试。
