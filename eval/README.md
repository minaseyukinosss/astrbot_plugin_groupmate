# Groupmate Phase 0 离线评测

该目录用于建立可重复的拟人化质量基线，不参与插件生产运行，也不会连接生产数据库。

## 按交互场景统计

`eval.scene_metrics` 按 `InteractionScene` 分组统计回复率、引用率、媒体率、
回复长度和延迟。指标必须在相同用户交互场景内比较；`overall` 只用于观察分布
漂移，不能作为运行时决定回复、引用或选择媒体的随机概率。

## 内容

- `scenarios/baseline.jsonl`：120 条版本化、脱敏的合成场景；
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
