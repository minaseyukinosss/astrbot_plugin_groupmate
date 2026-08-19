# Social Runtime v2 控制面运维手册

本手册覆盖 Phase D 控制面、Projection、SSE 和 AstrBot 插件页面。控制面是独立的 CQRS 管理面，不是群聊写线路的一部分。

## 上线边界

- 当前 Gate D 只证明控制面的隔离性、可审计性和页面安全性。
- `_conf_schema.json` 的 `control_admin_ids` 是服务端管理员用户名 allowlist；默认空列表会拒绝全部 Query、SSE 和 Command，不能用请求 body 或当前 username 动态扩权。
- 真实 QQ 发送保持禁用。Phase D 不改变 `SOCIAL_RUNTIME` 的生产发送 Gate，也不扩大任何群 allowlist。
- 只有后续阶段完成明确上线审批后，才能在指定测试群以外改变发送策略。
- 关系亲密度不提供管理员权限，也不能替代高影响命令确认。

## 数据流与职责

Journal 提交后，各 Projection Consumer 用独立 Cursor 构建只读模型。页面 Query 只读取这些模型；所有修改经 Command Service 校验服务端管理员身份、Persona/群作用域、Expected Version、原因和确认，然后以领域事件回到 Event Fabric。

Projection/SSE 故障不阻塞 GroupSceneActor、TaskRuntime 或 Outbox。页面不可用时，群聊事件、真实 Provider Event、任务恢复和事务交付仍按各自写线路运行。页面不得直接读取或修补这些写模型。

## 页面与实时更新

- 页面使用 AstrBot `window.AstrBotPluginPage` Bridge，不直接 `fetch`，也不保存浏览器凭据。
- SSE 事件只含 `cursor/kind/scope/entity/projection_version/summary`。重连携带 `Last-Event-ID`；Cursor 已超出保留窗口时，客户端重新加载 Snapshot。
- SSE 断开后页面降级为 15 秒有界轮询，并明确显示最多延迟。轮询不是健康状态，不得显示为实时已连接。
- Inspector 通过作用域内 Entity ref Query 获取单个隐私裁剪 Projection；不存在或越界统一返回 404。

## 管理命令

高影响命令必须有非空原因、二次确认和 Expected Version。普通治理动作使用权威 `control_version`；配置草稿、校验、Dry-run、发布和恢复使用当前已发布 `config_version`。

遇到 HTTP 409：

1. 不覆盖服务端状态，也不自动重复提交。
2. 重新加载对应 Governance Projection。
3. 向管理员展示当前版本和冲突影响。
4. 管理员复核新状态后，以新的 Expected Version 创建新的 command ID。

命令被 HTTP 202 接受只表示进入 Event Fabric；页面必须等待更高版本 Projection Event，不能乐观显示领域成功。

## 故障处置

### Projection 或 Query 失败

确认 `/health` 的 `degraded_reasons`，检查各 Projection Cursor 是否停止。可单独重建故障 Projection；不要暂停 Actor、取消任务或清理 Outbox。恢复后核对 `cursor/as_of/stale`。

### SSE 失败

确认页面显示 polling 和 15 秒影响，检查 `Last-Event-ID`。若服务端返回 `snapshot_required`，重新加载当前工作区全部 Snapshot，再建立订阅。

### Command 事件发布失败

HTTP 503 表示命令事件未可靠进入 Event Fabric。使用原 command ID 查询审计结果；只有服务端幂等结果明确时才重试，不能创建多个含义相同的高影响命令。

### Delivery unknown

平台发送结果为 unknown 时禁止盲目重试。保留 Outbox/Delivery Part 的未知状态，调查平台回执或由管理员明确处置，避免重复发送。

## 隐私与前端安全

- Projection 不包含 Secret、原始高权限 Event、内部 ID、提示词或 Chain-of-Thought。
- 动态内容只通过 `textContent` 渲染；不允许 HTML 执行 sink。
- 上传候选必须校验大小、MIME 和文件名；路径分隔符、`..`、控制字符、SVG/可执行类型及超过 5 MB 的文件全部拒绝。
- 跨群身份关联必须由管理员明确选择允许传递的数据类型；敏感经历和群关系不自动传播。

## Gate D 验收

Gate D 要求 contracts、page、recovery 测试，architecture_guard 和 `git diff --check` 全部通过，并人工确认五个 hash route、窄 iframe、200% 缩放、键盘焦点、明暗主题、Reduced Motion、SSE 降级、HTTP 409 和高影响确认流程。Gate D 不授权真实 QQ 发送。
