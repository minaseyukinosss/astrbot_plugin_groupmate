# Groupmate Phase 0 离线评测

该目录用于建立可重复的拟人化质量基线，不参与插件生产运行，也不会连接生产数据库。

## 按交互场景统计

`eval.scene_metrics` 按 `InteractionScene` 分组统计回复率、引用率、媒体率、
回复长度和延迟。指标必须在相同用户交互场景内比较；`overall` 只用于观察分布
漂移，不能作为运行时决定回复、引用或选择媒体的随机概率。

`eval.behavior_metrics` 进一步按 `scene` 与 `response act` 分组，统计条件回复率、
已回复前提下的媒体率、长度、延迟和安全违规。`scenarios/phase2_behavior.jsonl`
使用 `scene:*`、`act:*`、`media:*`、`capability:*` 标签描述用户行为应触发的
场景、行为和允许的输出形式。标签不是抽样权重，也不得转化为线上随机概率。

## 内容

- `scenarios/baseline.jsonl`：120 条版本化、脱敏的合成场景；
- `scenarios/phase2_behavior.jsonl`：场景—行为—能力组合的独立验收集；
- `schema.py`：场景与结果契约、隐私校验、Prompt 版本哈希；
- `runner.py`：deterministic/model 两种运行模式；
- `providers.py`：无第三方依赖的 OpenAI-compatible Provider；
- `scorers.py`：确定性 turn/conversation 评分与可选 LLM Judge；
- `rubrics/persona_judge.md`：人工和 LLM Judge 共用的评分标准；
- `build_corpus.py`：可重复生成仓库内的基线 JSONL。

## 确定性基线

```bash
python3 -m eval.runner \
  --mode deterministic \
  --output eval/results/baseline.json
```

该模式使用场景自带 scripted output，不联网，不消耗模型额度。它主要验证：

- TriggerRouter；
- Persona/ContextAssembly；
- CognitiveWorkflow；
- `<SILENCE>`；
- OutputFirewall；
- Delivery 与结构化评分。

默认即使部分质量检查失败也返回 0，因为 baseline 的职责是记录差距。CI 门禁可显式使用：

```bash
python3 -m eval.runner --mode deterministic --enforce
```

第二阶段行为集单独执行，避免改变 120 条基础集的组成：

```bash
python3 -m eval.runner \
  --mode deterministic \
  --enforce \
  --scenarios eval/scenarios/phase2_behavior.jsonl \
  --output /tmp/groupmate-phase2-behavior.json
```

## 真实模型模式

使用 OpenAI-compatible `/chat/completions`：

```bash
export GROUPMATE_EVAL_BASE_URL="https://provider.example/v1"
export GROUPMATE_EVAL_API_KEY="..."
export GROUPMATE_EVAL_MODEL="model-name"
export GROUPMATE_EVAL_TIMEOUT="60"
export GROUPMATE_EVAL_TEMPERATURE="0.4"

python3 -m eval.runner \
  --mode model \
  --repetitions 3 \
  --output eval/results/model-baseline.json
```

开启额外 LLM Judge 会为每次回复增加一次模型调用：

```bash
python3 -m eval.runner --mode model --judge --repetitions 3
```

运行前必须确认 Provider 费用和数据政策。API key 不写入 Prompt hash、结果或异常信息。

## 结果

报告包含：

- Prompt/Persona/Provider 公共配置的稳定版本 hash；
- 每次场景的 trigger、sent/silent、outcome、guard code 和检查项；
- turn-level 长度、必含/禁含模式和内部 ID 泄露；
- conversation-level 上下文保持、话题连续性和近重复；
- 分类通过率、沉默率、平均长度与延迟；
- 可选、单独保存的 LLM Judge 分数。

`eval/results/` 已被 Git 忽略。结果可能包含模型生成文本，不应提交仓库。

## 导出记录影子对齐

第三阶段工具只在本地读取 QQChatExporter 分片导出，提取“用户行为 -> 场景 ->
是否回复 -> 行为类型”的保守参考标签，再用当前插件的纯决策组件做影子投影。它不调用
模型或网络，不执行能力、发送和记忆副作用，也不改变线上插件配置。

```bash
export SHADOW_EXPORT_DIR="/absolute/path/to/local-export"
export SHADOW_TARGET_UIN="local-target-uin"

python3 -m eval.shadow_export \
  --export-dir "$SHADOW_EXPORT_DIR" \
  --target-uin "$SHADOW_TARGET_UIN" \
  --target-alias "目标别名" \
  --current-alias "爱弥斯" \
  --id-salt-file eval/results/.shadow-id-salt \
  --output eval/results/phase3-shadow.json \
  --markdown-output eval/results/phase3-shadow.md \
  --review-output eval/results/phase3-review.jsonl
```

可分享的 JSON/Markdown 报告只包含聚合统计、规则码和加盐匿名样本 ID。原始聊天
片段只进入 `phase3-review.jsonl`，该文件仅供本机人工复核。`eval/results/`、复核
文件和 `.shadow-id-salt` 都不得提交；盐一旦丢失，同一原始消息会得到不同匿名 ID。
所有盐、报告和复核输出路径必须位于源导出目录之外，且不得互相覆盖或覆盖人工
标签文件；命令会在创建任何文件前检查这条边界。

人工确认的复核项可通过 `--overrides path/to/overrides.jsonl` 提升为高置信标签。
每行必须是完整 JSON 对象，只允许以下字段：

```json
{"sample_id":"sample-anonymous-id","scene":"direct_address","act":"answer"}
```

`act` 对观察到的沉默必须为 `null`。覆盖文件中的样本 ID 必须存在于当前运行，重复、
未知或非法枚举会使整个运行失败。

报告的 `overall` 和各类型总体占比只用于发现数据分布与实现差距。运行时行为必须继续
由用户动作、指向关系、上下文和场景条件触发，禁止把目标占比转换成随机回复、随机引用
或随机媒体概率。

## 场景数据治理

场景必须：

- 使用 `g1/u1/m1` 等合成 ID；
- 不包含真实 QQ 号、登录凭据、链接 token 和私人地址；
- 只借鉴学习素材的节奏与场景结构，不照抄原句；
- 使用爱弥斯语境；
- 明确 expected action 和输出约束；
- 修改后重新执行 schema 测试和 deterministic baseline。

场景可以描述尚未达到的 V3 目标。质量失败属于基线结果；schema、Provider 或运行时错误才是评测基础设施失败。

## 人工盲评

1. 从 model report 按 category 分层抽样；
2. 隐去模型名、Prompt 版本和实现分支；
3. 至少两名评审按 `rubrics/persona_judge.md` 独立打分；
4. 先比较一致性，再讨论分歧；
5. LLM Judge 必须用人工标签校准，不得单独作为发布依据；
6. 线上失败样本脱敏后才能加入 corpus。

## 重新生成场景

```bash
python3 -m eval.build_corpus
```

生成后必须确认场景总数为 120，并运行完整测试。
