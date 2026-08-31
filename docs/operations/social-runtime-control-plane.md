# Social Runtime v2 控制面运维手册

本手册覆盖 Phase D 控制面、Projection、SSE 和 AstrBot 插件页面。控制面是独立的 CQRS 管理面，不是群聊写线路的一部分。

## 上线边界

- 当前 Gate D 只证明控制面的隔离性、可审计性和页面安全性。
- 控制面只接受 AstrBot 已认证的 WebUI 管理员身份；`request.username` 为空时全部拒绝。内部治理状态存在额外 allowlist 时继续收紧到该集合，不接受请求 body 声明管理员身份，群成员身份也不会获得控制权。
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
- `/profiles`、`/profile` 与 `/group-portrait` 继续复用同一管理员门禁和 `persona_id × group_id` 作用域。详情只返回不透明成员引用、整理后的事实、经历、关系和审计摘要；外群成员引用统一返回 404。

## 成员画像后台链路

群消息进入主链路时只做稳定身份记录和轻量观察入队，不同步调用画像模型。后台任务按配置的批量大小或间隔领取观察，模型返回结构化候选后再由本地证据策略确认、保留待确认或拒绝。画像模型不可用时，观察记录进入有界退避重试并保存安全诊断码；消息参与判断、回复交付和成员自查仍可继续。

画像状态包括：

- `confirmed`：证据达到本地规则，可进入有界回复上下文；
- `proposed`：证据不足或属于第三方说法，仅保留观察，不注入回复；
- `superseded` / `stale` / `rejected`：已被纠正、过时或失效，不再注入但保留审计历史。

成员画像和群友关系默认只在当前群生效。同一平台成员在另一个群不会自动继承事实、经历或关系；跨群身份处理必须由管理员执行明确的治理流程。

## 游戏知识 SHADOW 验收

游戏知识默认启用。预装范围为三角洲行动、鸣潮、崩坏：星穹铁道和绝区零；原神不再预装，但可像其他非预装游戏一样进入群热门学习。非预装游戏只有在同群滚动 7 天达到 8 次有效提及、3 名真实成员和 4 个场景后，才创建一次后台 discovery job；同一成员刷屏、Bot、转发、命令和跨群计数均不触发。后台至少需要两个独立公开来源同时支持游戏名称和同一稳定类型，才激活全局基础语义；单来源、搜索失败或冲突只重试，不生成版本事实。AMBIENT 搜索仍关闭。

知识管理查询只返回规范实体、群热度、claim 安全摘要、来源域名/类别、检查时间、群约定状态和 job 诊断码；不得返回原始 URL/query、excerpt、搜索请求、author ref 或原消息。纠正操作统一走 control command：管理员、群作用域、reason、expected version 和 request id 都必须有效；确认群约定只创建群 alias，不写全局 alias。驳回、替换和争议处理保留旧记录并追加 `origin_class=admin` observation 与 `governance_actions` 审计。手动 retry 只把已有 retry job 调整为当前到期，不在控制请求内访问网络。AMBIENT canary 的最新群级状态由 `knowledge.ambient_canary_enabled` 治理动作读取，默认关闭。

升级时只退休旧原神 bundled 投影和定时任务，不删除群内已学习知识。低信任观察 180 天后裁剪摘要，未被 claim 使用的搜索候选 30 天后裁剪 excerpt，审计哈希和关联保留。

上线前使用 `scenarios/game_knowledge_understanding.jsonl` 的 196 条冻结匿名样例运行 Gate 1。必须同时满足理解准确率至少 95%、高置信错误合并率低于 1%、跨群泄漏为 0、Bot/命令知识晋升为 0；版本指代还应全部判定为需要新鲜证据。运行中心抽查时只应看到规范名称、版本指代和限定诊断码，不应看到内部置信分、作者引用、证据 ID、原消息摘要或异常正文。

故障处置遵循以下边界：

- `knowledge_local_resolution_failed`：本轮使用空知识快照继续原有认知，不扩大回复权限；检查 seed 导入和 SQLite 状态。
- `knowledge_observation_failed`：只影响后台群约定学习，不阻塞消息入库、参与判断或交付；保留数据库后重启观察服务。
- 需要紧急回退时设置 `knowledge_enabled=false` 并重启；不要删除数据库，已有知识审计可保留待恢复。
- 新版本、发布日期、卡池、角色清单、数值、官方状态或爆料状态一律视为需要新鲜证据。进入下一阶段公开事实检索前，不得人工把未经准入的网页内容写成预装稳定语义。

## 管理命令

高影响命令必须有非空原因、二次确认和 Expected Version。普通治理动作使用权威 `control_version`；配置草稿、校验、Dry-run、发布和恢复使用当前已发布 `config_version`。

遇到 HTTP 409：

1. 不覆盖服务端状态，也不自动重复提交。
2. 重新加载对应 Governance Projection。
3. 向管理员展示当前版本和冲突影响。
4. 管理员复核新状态后，以新的 Expected Version 创建新的 command ID。

命令被 HTTP 202 接受只表示进入 Event Fabric；页面必须等待更高版本 Projection Event，不能乐观显示领域成功。

画像事实纠正与失效使用该成员当前的 `profile_revision`，不是全局 `control_version`。两者都是高影响命令，必须填写原因并二次确认。服务端再次验证管理员、群作用域、成员引用和事实引用；成功后旧事实立即停止注入、画像版本递增并写入 `profile_audit`。HTTP 409 时应重新读取该成员详情后再决定是否提交新命令。

## 成员说话风格蒸馏与临时模仿

- 设置以 `group_id × member_id` 为业务键，不使用 `persona_id` 制造重复风格资产。每成员默认关闭；`member_style_distillation_set` 只接受当前群不透明 `member_ref`、布尔开关、当前 `setting_version`、操作原因和管理员身份。
- 开启时刻会开始一个新收集窗口，不回填关闭期间的历史消息。关闭后资产立即不可选，并结束正在模仿该成员的会话。
- 成熟门槛为至少 40 条合格消息、5 个活跃日、3 类场景，其中至少 32 条为有实质内容的表达，实质消息占比不低于 75%。首版成熟后低频调用独立风格模型；此后至少新增 20 条合格消息才会发布新版本。
- 证据不接受命令、转发、链接或媒体正文、群复读、敏感/攻击内容、第三方话语。发布资产只保存起句、推进、收尾、节奏和分场景的定性特征；不保存可拼接的长原句，不把身份、经历、观点、关系、隐私或攻击对象写入风格。
- 启动/替换/停止要求平台真实 `@爱弥斯` 段。启动和管理员停止只认 `control_admin_ids`，不认 QQ 群职位；被模仿者只能结束当前群对自己的当前会话。目标先取同一消息的真实 @，其次只允许当前群不歧义的精确昵称/别名。
- 每群最多一个有效会话，必须有明确未来截止时间，单次最长 3 天。会话锁定启动时的风格版本，读取时自动排除已过期、已关闭蒸馏或版本不可用的会话。
- 表达附层只进入 `GENERATED` 闲聊回复。命令/插件结果、权限和安全文案、高风险确认、`EXACT_CHORUS` 复读保持原结果。身份守卫在一次修复仍失败后移除附层重试普通 Persona 表达，不改变已批准的 `SocialMovePlan` 和事实。
- 启动确认必须包含目标显示名、本地截止时间和当前 Persona 名，确认本身使用目标风格作为第一次试演。不使用连接、频道、上线、系统指令、浓度或百分比包装。确认模型失败时返回含同样三个事实的简短爱弥斯文本，会话事务不回滚。
- `/health` 只暴露风格任务是否启用/运行、当前群是否有会话、截止时间和有界诊断码；不暴露目标平台 ID、原始证据、模型输出或异常文本。

SQLite schema v3 对插件自有 v2 数据库执行原位迁移，新建库直接建立 `member_style_settings`、`member_speech_style_versions` 和 `imitation_sessions`。迁移不启用任何成员的蒸馏，也不创建模仿会话。

## 全新数据库切换

本流程只用于已经明确接受“画像、好感、场景、任务、审计等 V2 历史全部从空状态重新形成”的版本切换。插件自身永远不会删除或重置数据库；清空通过停机后移动文件完成，保留可回滚副本。不要在 AstrBot 仍运行时单独移动主文件，也不要只处理 `.db` 而遗漏 WAL/SHM。

### 切换前

1. 记录当前插件版本，并导出 AstrBot 中 Groupmate 的完整配置。API Key 等密钥应保存在既有密钥管理位置，不要写入工单、终端日志或仓库。
2. 停止 AstrBot，并确认没有进程继续打开数据库。停机方式以当前 AstrBot 部署（服务、容器或前台进程）为准。
3. 为本次操作填写一个不会重复的 `CUTOVER_TAG`，创建独立归档目录，然后对停止写入后的数据库执行 SQLite online backup 和完整性检查：

```bash
ASTRBOT_ROOT=/absolute/path/to/astrbot
DB_PATH="$ASTRBOT_ROOT/data/plugin_data/astrbot_plugin_groupmate/groupmate-social-runtime-v2.db"
CUTOVER_TAG=2026-08-27-before-fresh-profile
ARCHIVE_DIR="$ASTRBOT_ROOT/backups/groupmate-$CUTOVER_TAG"

mkdir -p "$ARCHIVE_DIR"
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(FULL);"
sqlite3 "$DB_PATH" ".backup '$ARCHIVE_DIR/groupmate-social-runtime-v2.backup.db'"
sqlite3 "$ARCHIVE_DIR/groupmate-social-runtime-v2.backup.db" "PRAGMA integrity_check;"
```

完整性检查必须返回 `ok`。若数据库不存在、检查失败或仍被写入，停止切换并先处理原因。

### 建立全新数据库

保持 AstrBot 停止，把三个 SQLite 运行文件移入同一个归档目录；某个辅助文件不存在时可以跳过，但不能删除其他文件：

```bash
for SUFFIX in "" "-wal" "-shm"; do
  if [ -e "$DB_PATH$SUFFIX" ]; then
    mv "$DB_PATH$SUFFIX" "$ARCHIVE_DIR/"
  fi
done
```

随后安装新插件版本、恢复刚才导出的插件配置，再启动 AstrBot。新版本会按当前 schema 创建新的 `groupmate-social-runtime-v2.db`，不会读取归档目录。

### 冒烟验收

切换后至少确认：

- AstrBot 和插件启动无 schema、认证或模型配置错误；
- `/health` 中 `profile_status.enabled` 与配置一致，启用画像时 `task_running` 为 true；
- 普通群聊能进入画像观察，后台处理后 `last_attempt_at`、`last_success_at` 会更新；合法但没有候选的批次显示 `profile_no_candidates`，不是静默无状态；
- 外部命令（例如配置给其他插件的 `bq`）不进入画像队列，也不触发闲聊回复；
- 成员侧只有精确命令 `查看我的画像` 可用，纠正和删除命令不开放；
- 管理页能看到成员画像、群画像和画像后台状态，且另一群的数据不会串入当前群。

保持原版本包、配置导出、`.backup.db` 和移动后的三个原始 SQLite 文件，至少跨过预定观察期后再按运维策略处理；不要在冒烟通过后立即删除。

### 回滚

1. 再次停止 AstrBot。
2. 把本次新建的 `.db`、`-wal`、`-shm` 移到另一个故障留存目录，不要覆盖切换前归档。
3. 恢复与旧数据库匹配的旧插件版本和旧配置。
4. 将 `groupmate-social-runtime-v2.backup.db` 复制回权威 `DB_PATH`，不要恢复旧 `-wal`/`-shm`；SQLite 会按需重新创建辅助文件。
5. 对恢复后的数据库运行 `PRAGMA integrity_check;`，启动 AstrBot，再核对 `/health`、待处理任务和 Outbox。`UNKNOWN` 或未确认的平台发送结果仍禁止盲重试。

如果旧版本无法读取恢复库，不要尝试手工改 schema；保持停机并使用归档的原插件版本、配置和数据库作为一个整体恢复。

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
