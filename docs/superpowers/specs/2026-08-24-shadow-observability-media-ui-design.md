# Groupmate SHADOW 可观测链路与运行中心修复设计

日期：2026-08-24

## 目标

本阶段把线上 SHADOW 从“最终全部显示为禁止发送”修正为可评估的真实决策链，同时修复运行中心中 @ 对象不可读、图片无法预览、手动刷新缺失和界面层级偏重的问题。

完成后，管理员应能从一条群消息看到：谁发送、消息包含什么、是否 @ 了谁、Groupmate 是否理解成功、拦截前准备做什么、为什么最终没有发送，以及任一降级发生在哪一层。

## 范围边界

本阶段包含：

- cognition worker 诊断与耗时持久化；
- SHADOW pre-gate 决策与 post-gate 交付结果分离；
- 候选目标、意图、评分与候选回复证据；
- @ 成员的私有解析和安全公开展示；
- 图片的受控缓存、预览、失败状态与重试；
- 运行中心即时刷新；
- 运行中心灰白中性视觉重整及深色、窄屏适配。

本阶段不包含：

- 开启真实群发送或扩大 allowlist；
- 视频解析、群管理、表情包等能力插件内迁；
- 重写整个控制台框架或新增无关页面；
- 在浏览器暴露 QQ 号、原始 OneBot 媒体 URL、文件路径或模型内部推理。

## 设计原则

1. **决策真实，发送禁用**：SHADOW 与 SOCIAL_RUNTIME 走相同认知和治理链，仅在最终交付门分叉。
2. **正常沉默不等于系统降级**：`SILENCE`、`OBSERVE`、worker 超时和模型失败必须分别呈现。
3. **身份与媒体默认私有**：QQ ID 和远程 URL 只存在于服务端私有目录；页面只接收作用域内的 opaque ref 和安全展示数据。
4. **失败可解释、可重试**：媒体和认知失败不能退化为无说明的标签。
5. **手动刷新不破坏上下文**：刷新数据而非整页重载，保留筛选、搜索和当前详情。
6. **UI 服务于判断**：中性灰白是基础，状态色只用于表达运行、警告、异常和阻断。

## 1. 目标数据流

```text
NapCat / OneBot group message
  -> AstrBotEventTranslator（平台事实）
  -> ParticipantDirectory / MessageMediaDirectory（私有解析与缓存）
  -> MessageTrace RECEIVED
  -> Ownership Gate
  -> Attention
  -> Cognition workers + diagnostics
  -> Candidate intentions
  -> SocialGovernor pre-gate decision
  -> Candidate reply planning/generation when applicable
  -> SHADOW delivery gate
      -> SHADOW: BLOCKED_BY_SHADOW，永不写入发送 Outbox
      -> SOCIAL_RUNTIME: 继续现有发送治理
  -> MessageTrace / ShadowReview projection
  -> 运行中心
```

`force_observe` 不再用于表达“因为当前是 SHADOW 所以禁止发送”。它只保留为认知或治理层的保守结果。SHADOW 的 no-send 是独立、可审计的交付结果。

## 2. 认知诊断

### 2.1 Worker 运行记录

每个 cognition worker 产生结构化诊断：

- `worker`
- `status`: `SUCCEEDED | TIMED_OUT | MODEL_FAILED | INVALID_OUTPUT | REJECTED`
- `started_at`
- `completed_at`
- `latency_ms`
- `diagnostic_code`

诊断只包含稳定错误码，不持久化异常堆栈、模型原文或隐私上下文。聚合诊断进入事件流和详情页，支持判断 Provider 过慢、格式无效或 worker 缺失。

### 2.2 超时语义

默认 10 秒超时先保持不变。首个实现目标是让超时可证实，而不是直接放宽阈值。后续如果线上诊断证明 Provider 正常完成时间稳定超过预算，再通过显式配置调整。

worker 超时导致的 blackboard degraded 必须显示为“认知降级：worker 超时”，不能显示为“Groupmate 主动选择继续观察”。

## 3. SHADOW 双阶段决策

### 3.1 Pre-gate

在最终交付门之前保存：

- `outcome`: `ACT | DEFER | OBSERVE | SILENCE`
- `target_ref` 与安全显示名称；
- 选中的 `intention_id`、`kind`、`proposed_act`；
- governor utility 及组成分数；
- reason codes 与 constraints；
- cognition diagnostics；
- 候选回复文本与 modality；
- 生成失败原因。

候选回复只在 pre-gate 为 `ACT` 且候选意图有效时生成。它通过已有 StyleDirector 与 OutputFirewall，但不得进入真实发送 Outbox。

### 3.2 Post-gate

单独保存：

- `runtime_mode`
- `delivery_outcome`: `BLOCKED_BY_SHADOW | SILENT | DEFERRED | READY | SENT | FAILED | UNKNOWN`
- `delivery_reason`

SHADOW 下 pre-gate `ACT` 的页面语义为“本来准备回复 / SHADOW 未发送”。正常 `SILENCE` 的页面语义为“判断为不参与 / 无需发送”。认知降级则显示单独异常状态。

### 3.3 兼容现有数据

新字段以附加、可选方式进入投影。旧记录缺少 pre-gate 或 diagnostics 时显示“旧版记录未采集”，不伪造推断。数据库迁移只新增字段或 JSON 内容，不破坏已采集的 408 条线上记录。

## 4. @ 成员解析

### 4.1 私有存储

`AstrBotEventTranslator` 保留 OneBot `at.data.qq` 事实，但公开 trace 不直接输出 QQ 号。`ParticipantDirectory` 增加按 `(persona_id, group_id, actor_id)` 查询或补全成员的能力。

收到 @ segment 时：

1. 识别 `qq=all`，公开显示“@全体成员”；
2. 若成员目录已有映射，输出 `member_ref`、`display_name`、`avatar_ref`；
3. 若尚无映射，尝试通过 AstrBot/NapCat 成员查询端口补全；
4. 查询失败时显示“@未知成员”，保留安全状态，不展示原始 ID。

### 4.2 页面展示

列表摘要显示 `@昵称`；详情显示被 @ 成员的头像、昵称和“提及”关系。多个 @ 按原消息顺序展示，重复对象去重仅用于摘要，不修改原 segment 顺序。

## 5. 图片预览

### 5.1 服务端受控缓存

原始 QQ 媒体 URL 继续存入私有 `message_media`。图片在消息接收后尽早进入受控缓存，避免管理员稍后打开详情时 URL 已过期。

缓存规则：

- 只允许 `http/https` 且解析到公网地址；
- 限制 MIME、大小、跳转目标和读取超时；
- 缓存内容使用 opaque `media_ref` 索引；
- 页面仍通过 `/media` 获取 data URI，不接触远程 URL；
- 缓存失败保存 `FETCH_FAILED | EXPIRED | UNSUPPORTED | TOO_LARGE` 等稳定状态；
- 管理员可在详情页重试一次，不自动无限重试。

实时接收路径不能被下载阻塞。缓存任务由 Bridge 管理的有界后台任务执行，关闭插件时取消并回收。

### 5.2 页面展示

- 列表：图片显示约 40px 的裁切缩略图并保留“图片”文字语义；
- 详情：按原比例展示，限制最大宽高，可点击打开安全预览；
- 加载中：固定尺寸骨架，避免布局跳动；
- 失败：显示具体可理解原因与“重试加载”，不再静默只显示“图片”。

## 6. 即时刷新

“消息链路”标题右侧增加刷新按钮，使用现有图标资产和中性按钮样式。

点击后并行重新请求当前运行中心依赖的 `runtime`、`traces`、`health`、`persona` 和 `governance` 投影。刷新期间：

- 按钮禁用并呈现旋转状态；
- 不清空当前内容；
- 保留搜索、筛选、加载条数和已打开的详情；
- 成功后显示“刚刚更新”；
- 失败时保留旧数据并使用现有错误区提示；
- 连续点击只复用同一个 in-flight Promise。

SSE 和 15 秒 polling 保持不变；即时刷新是管理员主动拉取入口，不替代实时链路。

## 7. 运行中心视觉设计

参考图 3 的视觉语言，但保持 Groupmate 的信息架构：

- 页面画布使用中性浅灰，内容区域使用白色；
- 侧栏与主内容通过背景层级分隔，不使用大面积绿色；
- 选中导航为浅灰底和深色文字；
- 面板圆角限制在 12–14px，主要依赖细边框，不叠加宽泛阴影；
- 输入和按钮为浅灰边框、白底、明确 hover/focus；
- 绿色仅表达在线、成功和 SHADOW 正常运行；蓝色用于信息，琥珀/红色用于警告和失败；
- 字体继续使用现有系统字体栈；
- 密度保持适合事件流阅读，正文不缩到难以辨认；
- 深色主题保持同一层级关系；
- 70rem 以下详情变为覆盖抽屉，44rem 以下事件行变为单列卡片。

## 8. 错误处理与安全

- 认知失败必须 fail closed，不产生真实发送；
- SHADOW 候选回复不写入 Outbox；
- 页面不得根据 QQ ID 拼接头像或媒体地址；
- opaque ref 必须按 persona/group scope 校验；
- media retry 复用相同 SSRF、MIME 和大小限制；
- 手动刷新失败不清空最后一次成功快照；
- 旧数据和字段缺失使用明确 unavailable 状态。

## 9. 最小验证策略

遵循少量、针对性验证：

1. cognition worker 超时会写入结构化诊断，正常完成不会被标为 degraded；
2. SHADOW pre-gate `ACT` 能保存候选目标、意图和回复，但 Outbox 保持为空；
3. 正常 `SILENCE`、认知降级和 `BLOCKED_BY_SHADOW` 在投影中互不混淆；
4. @ 已知成员显示昵称与头像引用，未知成员不泄露 QQ 号；
5. 图片缓存成功可通过 scoped `/media` 返回，失败显示稳定错误并可重试；
6. 即时刷新只发起一轮请求并保留筛选/详情；
7. 运行中心在桌面宽度、窄 iframe、浅色和深色下通过一次视觉检查。

不运行全量测试矩阵。开发中只运行相关 contract/page/scenario 测试，完成时执行前端构建或静态契约检查及一次浏览器验证。

## 10. 实施顺序

1. 增加 worker diagnostics contract，并接入 CognitionService 与持久化投影；
2. 拆分 pre-gate decision 和 post-gate delivery，补候选回复证据；
3. 扩展 ParticipantDirectory 和 @ segment 公共表示；
4. 实现有界图片预取、缓存状态和重试 API；
5. 更新运行中心数据展示与详情；
6. 增加即时刷新；
7. 重整 tokens、布局和组件视觉；
8. 执行少量针对性测试和浏览器视觉验证。
