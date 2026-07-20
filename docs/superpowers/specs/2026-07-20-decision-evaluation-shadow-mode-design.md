# Groupmate 决策评测与影子模式设计规格

日期：2026-07-20
状态：已确认，待实施
适用版本：AstrBot Groupmate 0.2

## 1. 背景

Groupmate 已具备消息观察、确定性触发路由、每群串行 Actor、决策模型、人格生成、输出校验和持久化决策轨迹，但当前只能证明各个模块按照单元测试工作，无法回答以下产品问题：

- Bot 是否在应该回应时可靠醒来；
- Bot 是否在群友已经正常交流时错误插话；
- 不同决策模型、阈值和防抖配置哪个更适合目标群；
- 主动回复数量是否落在每小时 3～6 条的目标范围；
- 一次策略修改究竟改善了效果，还是只改变了回复数量；
- 真实群环境中的判断结果与离线测试是否一致。

因此下一阶段先建设可重复执行的决策评测体系，不直接增加长期记忆、情绪、WebUI、TTS 或复杂注意力功能。后续行为优化必须通过本评测体系证明效果。

## 2. 设计依据

本设计采用成熟智能体运行时中的以下原则：

- 将认知流程拆成边界明确、可独立重放的步骤；
- 保存结构化状态和原因码，而不是保存模型的隐式思维链；
- 把“是否介入”与“如何回复”分开评测；
- 在生产环境先以影子模式观察新策略，不直接影响群聊；
- 使用固定数据集和配置快照保证不同版本之间可以比较；
- 先验证高精度的说话时机，再允许自动学习写入长期记忆。

Groupmate 保持现有模块化单体和端口适配架构，不引入 LangGraph、Temporal、Letta 或新的多智能体框架依赖。只吸收它们的显式状态、检查点、回放、分层记忆和可观测性思想。

## 3. 目标

本阶段交付以下能力：

1. 从标准评测 JSONL 重放多成员群聊窗口。
2. 使用当前 `TriggerRouter` 和决策模型生成结构化预测。
3. 在不发送消息的前提下统计介入准确率、唤醒召回率、指令旁路率和发言频率。
4. 支持两个或多个决策配置在同一数据集上的结果对比。
5. 支持 QQ + NapCat 实时影子模式，只记录决策，不生成最终回复、不调用视觉模型、不发送消息。
6. 保存足够的版本、配置和输入摘要，使单次评测可以复现。
7. 为后续注意力、记忆和人格学习提供统一验收门槛。

## 4. 非目标

本阶段不实现：

- 自动标注真实群聊；
- 使用现有单 Bot 导出推断群友上下文；
- 自动训练或微调决策模型；
- 向量数据库、知识图谱或无约束长期记忆；
- 自动修改人格提示词；
- 对最终回复文本进行主观质量打分；
- Web 管理面板；
- 多智能体讨论或评委投票；
- 将影子决策自动切换为真实发送。

## 5. 数据事实与限制

本系统不依赖用户后续提供 QQChatExporter、`c000001.jsonl` 或其他离线聊天导出。现有单 Bot 学习素材只作为一次性人格设计参考，不进入介入评测、构建流程或运行时依赖。

介入评测样本只来自两条内生链路：

- 仓库内人工编写并审核的关键场景，用于确定性唤醒、指令、噪声和沉默测试；
- 插件从 AstrBot 实时群事件中自行构造的有限影子窗口，经过本地脱敏和管理员标注后进入评测集。

NapCat 的 `get_group_msg_history` 只在插件首次接触群时补充最多 100 条近期上下文，帮助理解刚刚发生的话题。它不会批量导出历史，也不会被视为已经标注的标准答案。

群聊历史只能提供“发生了什么”，不能自动证明“Bot 应不应该说话”。普通候选必须由管理员标注，或保持 `unlabeled`；系统不得把自身预测回写成正确答案。

## 6. 总体架构

```text
                         ┌────────────────────┐
内置黄金场景 JSONL ─────→│ OfflineReplayRunner │
                         └─────────┬──────────┘
                                   │ ChatMessage
                                   ▼
                           TriggerRouter
                                   │
                                   ▼
                          DecisionEvaluation
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
              PredictionRecord               MetricReport

AstrBot/NapCat 实时事件 ─→ GroupActor ─→ ShadowDecisionProbe ─┬→ ShadowRecord
                                                             │       │
                                                             │       └→ 本地审阅与标注
                                                             └→ 禁止生成、视觉和发送
```

离线回放和在线影子模式共享领域消息、触发类型、决策结构、原因码和记录格式，但入口和时钟不同：

- 离线回放使用数据中的时间戳和确定性虚拟时钟；
- 在线影子模式使用系统时钟和真实群消息；
- 两者都不能通过 `PlatformPort` 发送消息。

## 7. 模块边界

新增 `groupmate/evaluation/` 包：

- `models.py`：标签、评测样本、预测、运行元数据和指标类型；
- `dataset.py`：标准 JSONL 的读取、校验和写出；
- `replay.py`：按时间顺序重放消息并构造话题窗口；
- `evaluator.py`：运行触发路由和决策端口，生成预测；
- `metrics.py`：聚合指标和配置对比；
- `report.py`：输出中文 JSON 与 Markdown 报告；
- `cli.py`：离线评测命令入口；
- `collector.py`：从实时规范化消息构造有限影子窗口；
- `shadow.py`：在线影子记录器和持久化接口。

现有模块只进行边界必要的最小修改：

- `models.py` 增加不产生回复的决策探测结果类型，或由评测包自行包装现有 `Decision`；
- `astrbot_adapter.py` 在 Bridge 中注入可选影子记录器；
- `config.py` 和 `_conf_schema.json` 增加影子模式配置；
- `main.py` 的状态命令展示影子模式状态；
- `memory.py` 增加影子记录表和查询接口。

评测包不得导入 AstrBot。只有 `astrbot_adapter.py` 可以把在线事件转交给影子记录器。

## 8. 标准评测数据格式

每行是一个完整、独立的评测场景：

```json
{
  "schema_version": 1,
  "case_id": "wake-alias-001",
  "description": "群友直接呼叫 Bot 别名",
  "messages": [
    {
      "message_id": "m1",
      "group_id": "eval-group",
      "sender_id": "u1",
      "sender_name": "群友甲",
      "text": "小爱，在吗",
      "timestamp": 1000,
      "is_command": false,
      "mentions_bot": false,
      "reply_to_bot": false,
      "is_bot": false,
      "image_urls": [],
      "segment_types": ["text"]
    }
  ],
  "expected": {
    "label": "must_respond",
    "allowed_triggers": ["alias_direct"],
    "allowed_reason_codes": ["alias_direct"],
    "target_message_id": "m1"
  },
  "tags": ["wake", "alias", "critical"],
  "source": "handcrafted"
}
```

标签为封闭枚举：

- `must_respond`：必须进入回复路径；
- `may_respond`：回复或沉默都可接受，只用于频率和风格观察，不进入严格准确率；
- `must_silence`：必须保持沉默；
- `native_wake`：必须旁路给 AstrBot 原生唤醒链路；
- `command_bypass`：必须识别为指令并旁路；
- `invalid_input`：输入应被拒绝，不进入模型判断。

每个场景必须有稳定 `case_id`。同一数据集内重复 ID 视为格式错误。所有消息必须属于同一个群，时间戳必须单调不降。

## 9. 预测记录格式

每个场景产生一条 `PredictionRecord`：

```json
{
  "case_id": "wake-alias-001",
  "trigger": "alias_direct",
  "action": "respond",
  "confidence": 1.0,
  "reason_code": "alias_direct",
  "target_message_id": "m1",
  "decision_model_called": false,
  "latency_ms": 1,
  "error_code": null,
  "matched": true
}
```

记录不得包含模型隐式思维链。模型输出可以保留经过结构化解析后的字段；原始响应默认不落盘，只有显式启用诊断并完成脱敏后才允许保留。

## 10. 离线回放语义

`OfflineReplayRunner` 必须满足：

1. 使用消息时间戳推进虚拟时钟，不读取系统当前时间。
2. 按场景顺序将消息提交到与生产一致的 `TopicWindow` 和 `TriggerRouter`。
3. 防抖不通过真实 `sleep` 实现，而由虚拟调度器立即推进到预定时间。
4. 指令、原生唤醒、直接别名和普通候选必须遵守生产路由优先级。
5. 普通候选允许调用注入的 `DecisionModelPort`。
6. 评测结束后不得残留异步任务。
7. 同一数据、配置和确定性模型必须得到字节级一致的预测 JSON。

离线评测默认只运行到决策门控，不调用人格生成、视觉模型和平台发送。回复文本质量将在后续独立评测子系统中处理。

## 11. 在线影子模式

新增配置：

- `shadow_mode`：默认 `false`；
- `shadow_sample_rate`：默认 `1.0`，范围 0.0～1.0；
- `shadow_retention_days`：默认 `7`，范围 1～30；
- `shadow_store_message_text`：默认 `false`；
- `shadow_hash_sender_id`：固定为 `true`，首版不允许关闭。

影子模式启用后：

1. 继续执行消息规范化、触发分类、话题聚合、频率预检和决策模型判断。
2. 到达 `GATE` 后立即结束，不进入 `PLAN`、`GENERATE`、`VISION`、`GUARD`、`OUTBOX` 或 `SEND`。
3. 原生唤醒和既有指令仍由 AstrBot 正常处理，Groupmate 只记录旁路结论。
4. 插件直接别名唤醒也不得由 Groupmate 发送，以保证影子模式严格零发送。
5. 影子模式不消耗正式主动回复额度，但必须记录“如果正式运行是否会被额度拦截”。
6. 配置不能自动从影子模式切换到正式模式，必须由管理员显式关闭。

影子样本由插件自行构造：

- 每个候选保留最多 20 条规范化消息；
- 窗口跨度不超过 5 分钟；
- 自动移除 OneBot 原始 metadata、资源 URL、文件路径和未使用字段；
- `shadow_store_message_text=false` 时只保存非文本特征，用于运行统计；
- `shadow_store_message_text=true` 时保存本地脱敏上下文，允许管理员完成介入标注；
- 已标注记录可以由插件自身转换为标准评测场景，不需要外部聊天导出。

影子记录包括：

- 群的不可逆哈希标识；
- 发送者不可逆哈希标识；
- 时间桶；
- 触发类型；
- 决策结构化字段；
- 策略与模型版本；
- 消息长度、媒体类型、参与人数和回复链特征；
- 可选的脱敏消息文本；
- 人工标签与标注时间；
- 错误码和耗时。

## 12. 指标定义

严格标签集合不包含 `may_respond`。

### 12.1 唤醒指标

- 直接唤醒召回率：标记为 `must_respond` 且带 `wake` 标签的场景中，进入回复路径的比例；
- 原生唤醒旁路率：`native_wake` 场景被识别为 `native_direct` 的比例；
- 指令旁路率：`command_bypass` 场景被识别为 `command` 的比例。

### 12.2 主动参与指标

- 主动介入精确率：预测主动回复的普通候选中，标签为 `must_respond` 的比例；
- 主动介入召回率：`must_respond` 普通候选中，被预测回复的比例；
- 错误插话率：`must_silence` 中被预测回复的比例；
- 沉默准确率：`must_silence` 中保持沉默的比例；
- 每小时预测回复数：按群和虚拟时间聚合，不包含直接唤醒；
- 决策模型调用率：普通输入中真正调用模型的比例，用于衡量前置规则节省的成本。

### 12.3 稳定性指标

- 决策结构解析成功率；
- 决策异常率；
- P50、P95 决策耗时；
- 重复预测率；
- 原因码分布；
- 数据集与配置相同时的结果一致性。

首版验收门槛：

- 直接唤醒召回率等于 100%；
- 原生唤醒旁路率等于 100%；
- 指令旁路率等于 100%；
- 影子模式由 Groupmate 发送的消息数等于 0；
- 决策结构解析成功率不低于 99%；
- 在人工标注集达到至少 100 个严格场景后，主动介入精确率目标不低于 85%；
- 重复预测率低于 1%。

在严格场景不足 100 个时，报告必须标记为“样本不足”，不得宣称达到产品质量门槛。

## 13. 配置对比

评测运行使用不可变 `EvaluationConfig`，至少记录：

- 配置名称；
- Git commit；
- 决策 Provider 标识；
- Prompt 版本；
- 决策阈值；
- 防抖范围；
- 每小时额度和冷却；
- Bot 别名；
- 数据集内容哈希；
- 随机种子；
- 运行时间。

对比报告必须在同一数据集哈希上比较配置。数据集不同则只并列展示，不计算提升百分比。

默认报告展示：

- 总体指标；
- 按标签和标签组拆分的指标；
- 两个配置之间改善和退化的场景列表；
- 新增错误插话；
- 修复的漏唤醒；
- 原因码和模型调用率变化。

## 14. 持久化

SQLite 新增 `shadow_decisions` 表：

- `id`：自增主键；
- `decision_id`：唯一决策标识；
- `group_hash`：群标识哈希；
- `sender_hash`：发送者标识哈希；
- `trigger`：触发类型；
- `action`：预测行为；
- `confidence`：置信度；
- `reason_code`：原因码；
- `would_rate_limit`：正式运行是否会被额度拦截；
- `features_json`：非文本特征；
- `context_json`：默认空；启用本地审阅时保存脱敏后的有限消息窗口；
- `label`：管理员标签，默认 `unlabeled`；
- `labeled_at`：标注时间；
- `model_id`：决策模型标识；
- `policy_version`：策略版本；
- `latency_ms`：耗时；
- `error_code`：错误码；
- `created_at`：创建时间；
- `expires_at`：过期时间。

启动时和每日首次写入时清理过期影子记录。迁移版本从 1 升至 2，迁移必须可重复执行。

## 15. 隐私与安全

- 默认不保存消息正文；
- QQ 号、群号和内部 UID 不得写入影子表；
- 哈希使用插件本地随机盐和 HMAC-SHA256，不能使用可枚举的裸 SHA256；
- 本地盐存放在插件数据目录，不进入日志和仓库；
- 报告默认只输出 `case_id` 和哈希标识；
- 输入中的群消息始终被视为不可信数据，不能作为系统指令执行；
- 导出影子数据前必须再次移除原始 metadata、URL、文件路径和内部 ID；
- 管理命令不得直接展示消息正文；
- 评测数据提交仓库前必须使用虚构身份或不可逆脱敏值。

## 16. 错误处理

- 数据集结构错误：立即终止并报告行号和字段，不跳过；
- 重复 `case_id`：立即终止；
- 单条消息字段非法：立即终止对应运行；
- 决策模型超时：记录 `decision_timeout`，安全预测为沉默；
- 决策模型异常：记录 `decision_error`，安全预测为沉默；
- 决策 JSON 无效：记录 `invalid_decision_schema`，安全预测为沉默；
- 影子数据库写入失败：记录 AstrBot 错误日志，但不得影响原生指令或唤醒链路；
- 报告写入失败：命令返回非零退出码，不输出部分成功提示；
- 模型标识缺失：允许运行确定性路由用例，普通候选记录 `decision_provider_missing`。

## 17. 命令接口

离线命令：

```bash
python -m groupmate.evaluation.cli run \
  --dataset tests/fixtures/evaluation/golden.jsonl \
  --config tests/fixtures/evaluation/default.json \
  --output .artifacts/evaluation/default
```

配置对比：

```bash
python -m groupmate.evaluation.cli compare \
  --baseline .artifacts/evaluation/default/result.json \
  --candidate .artifacts/evaluation/candidate/result.json \
  --output .artifacts/evaluation/comparison.md
```

数据集校验：

```bash
python -m groupmate.evaluation.cli validate \
  --dataset tests/fixtures/evaluation/golden.jsonl
```

AstrBot 管理命令扩展：

- `/groupmate_status`：显示影子模式、记录数量和最近错误；
- `/groupmate_shadow_stats`：显示不含正文的聚合统计；
- `/groupmate_shadow_label <decision_id> <必须回复|可以回复|必须沉默|跳过>`：给本地影子记录增加人工标签；
- 不增加聊天内导出原始影子数据的命令。

## 18. 测试策略

### 18.1 数据契约测试

- 正常读取版本 1 数据集；
- 拒绝未知标签、重复 ID、跨群消息和时间倒序；
- 输出记录可序列化并稳定排序；
- 数据集哈希不受文件路径影响。

### 18.2 回放测试

- 直接别名无需调用决策模型；
- 原生唤醒和指令正确旁路；
- 普通候选只在防抖窗口结束后判断一次；
- 虚拟时钟不调用真实 `sleep`；
- 决策异常安全沉默；
- 相同输入与种子得到相同结果。

### 18.3 指标测试

- 使用手工小矩阵验证混淆矩阵；
- `may_respond` 不进入严格准确率；
- 直接唤醒不计入主动发言频率；
- 样本不足时报告明确标识；
- 不同数据集哈希不能计算提升率。

### 18.4 影子模式测试

- 所有触发类型下平台发送调用次数均为零；
- 不调用生成模型和视觉模型；
- 不消耗正式频率额度；
- 原生指令链路不受影子记录失败影响；
- 默认记录不包含消息正文、QQ 号、群号和原始 metadata；
- 启用审阅文本时只保存规范化后的有限窗口，不保存 OneBot 原始事件；
- 管理员标签不能修改原始预测，只能作为独立真值字段写入；
- 过期记录自动清理；
- 迁移重复执行不报错。

### 18.5 回归验证

完成后必须运行：

```bash
python3 -m pytest -q
python3 -m compileall -q main.py groupmate tests
python3 -m json.tool _conf_schema.json >/dev/null
python3 -m groupmate.evaluation.cli validate \
  --dataset tests/fixtures/evaluation/golden.jsonl
```

## 19. 交付顺序

1. 定义评测数据与预测类型。
2. 实现数据集校验和稳定哈希。
3. 实现虚拟时钟与离线回放器。
4. 实现指标聚合和报告。
5. 建立不少于 30 个手工关键场景，覆盖唤醒、指令、沉默和普通候选。
6. 实现影子模式领域边界，确保零生成、零视觉、零发送。
7. 增加 SQLite 影子记录、HMAC 脱敏和保留期清理。
8. 接入 AstrBot 配置与管理员统计命令。
9. 使用真实决策 Provider 在内置黄金场景上执行一次离线对比。
10. 在测试群运行影子模式，由插件自行采集待人工标注窗口。

## 20. 后续阶段门槛

只有完成本阶段并获得至少 100 个严格标注场景后，才进入下一阶段：

1. 修复和强化输出修复、视觉 Provider 回退、Outbox 恢复与 Prompt 隔离；
2. 根据错误插话分布设计轻量注意力状态；
3. 根据漏唤醒和关系错误引入受审查的社交记忆；
4. 根据实际运维需求建设 WebUI、图片缓存、免打扰和 TTS。

新增机制必须在固定评测集上满足：不降低直接唤醒和指令旁路指标，并且不能以明显增加错误插话换取召回率。

## 21. 验收标准

本阶段完成时必须满足：

- 评测包不依赖 AstrBot，可在纯 Python 环境运行；
- 标准 JSONL 可以校验、回放并生成中文 JSON/Markdown 报告；
- 相同输入、配置和种子得到一致结果；
- 至少 30 个手工场景进入仓库并全部通过；
- 可对两个配置生成逐场景差异报告；
- 影子模式不会调用回复生成、视觉或平台发送；
- 影子记录默认不包含可直接识别的 QQ 号、群号或消息正文；
- 不提供任何外部聊天导出时，插件仍能通过内置场景和实时影子采集完成整个评测流程；
- 所有现有测试继续通过；
- 文档、配置说明和管理命令说明均使用中文。
