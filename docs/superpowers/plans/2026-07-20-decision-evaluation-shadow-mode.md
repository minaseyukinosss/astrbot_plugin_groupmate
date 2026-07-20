# Groupmate 决策评测与影子模式实施计划

> **供智能体执行者使用：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐项执行。步骤使用复选框跟踪。

**目标：** 在不依赖用户提供聊天导出的前提下，为 Groupmate 增加内置黄金场景回放、中文指标报告和由 AstrBot 实时消息自行采集的零发送影子模式。

**架构：** 新增不依赖 AstrBot 的 `groupmate.evaluation` 包，复用现有领域消息、触发路由和决策接口。生产侧通过 `ShadowWorkflow` 将每群 Actor 截止在决策门控，结构化结果经 HMAC 脱敏后写入 SQLite；正式工作流保持原有生成和发送路径。

**技术栈：** Python 3.10+、`asyncio`、`dataclasses`、`enum`、`hashlib`、`hmac`、`json`、`sqlite3`、AstrBot 4.24+、pytest。

---

## 文件结构

- `groupmate/evaluation/__init__.py`：公开评测类型。
- `groupmate/evaluation/models.py`：标签、场景、预测和报告数据类型。
- `groupmate/evaluation/dataset.py`：JSONL 校验、规范化和稳定哈希。
- `groupmate/evaluation/evaluator.py`：确定性触发与决策门控回放。
- `groupmate/evaluation/metrics.py`：严格指标和样本充足性计算。
- `groupmate/evaluation/report.py`：稳定 JSON 与中文 Markdown 报告。
- `groupmate/evaluation/cli.py`：`validate`、`run`、`compare` 命令。
- `groupmate/evaluation/collector.py`：影子窗口特征和可选脱敏文本。
- `groupmate/evaluation/shadow.py`：零发送工作流和 HMAC 身份散列。
- `tests/fixtures/evaluation/golden.jsonl`：不少于 30 个虚构身份关键场景。
- `tests/fixtures/evaluation/default.json`：可复现默认配置。

## 任务 1：评测数据契约

**文件：**

- 创建 `groupmate/evaluation/__init__.py`
- 创建 `groupmate/evaluation/models.py`
- 创建 `groupmate/evaluation/dataset.py`
- 创建 `tests/test_evaluation_dataset.py`

- [ ] **步骤 1：编写失败测试**

测试必须覆盖正常读取、重复 `case_id`、跨群消息、时间倒序、未知标签和稳定内容哈希：

```python
def test_dataset_hash_is_independent_of_file_path(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    payload = json.dumps(valid_case_dict(), ensure_ascii=False) + "\n"
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")
    assert load_dataset(first).content_hash == load_dataset(second).content_hash


def test_duplicate_case_id_is_rejected(tmp_path):
    path = write_cases(tmp_path, [valid_case_dict(), valid_case_dict()])
    with pytest.raises(DatasetValidationError, match="case_id 重复"):
        load_dataset(path)
```

- [ ] **步骤 2：确认测试按预期失败**

运行：`python3 -m pytest tests/test_evaluation_dataset.py -q`

预期：因 `groupmate.evaluation` 不存在而失败。

- [ ] **步骤 3：实现最小数据类型和校验器**

定义以下稳定接口：

```python
class EvaluationLabel(str, Enum):
    MUST_RESPOND = "must_respond"
    MAY_RESPOND = "may_respond"
    MUST_SILENCE = "must_silence"
    NATIVE_WAKE = "native_wake"
    COMMAND_BYPASS = "command_bypass"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class ExpectedOutcome:
    label: EvaluationLabel
    allowed_triggers: tuple[TriggerKind, ...] = ()
    allowed_reason_codes: tuple[str, ...] = ()
    target_message_id: str | None = None


@dataclass(frozen=True)
class EvaluationCase:
    schema_version: int
    case_id: str
    description: str
    messages: tuple[ChatMessage, ...]
    expected: ExpectedOutcome
    tags: tuple[str, ...]
    source: str
```

`load_dataset(path)` 返回包含 `cases` 和 SHA-256 `content_hash` 的不可变对象。哈希基于规范化内容，不包含文件路径。

- [ ] **步骤 4：运行测试并提交**

运行：`python3 -m pytest tests/test_evaluation_dataset.py -q`

提交：`feat: add evaluation dataset contracts`

## 任务 2：离线决策回放器

**文件：**

- 创建 `groupmate/evaluation/evaluator.py`
- 创建 `tests/test_evaluation_replay.py`

- [ ] **步骤 1：编写失败测试**

```python
@pytest.mark.asyncio
async def test_alias_direct_does_not_call_decision_model():
    model = FakeDecisionModel(Decision.ignore("unused"))
    result = await DecisionEvaluator(model, GroupPolicy()).evaluate(alias_case())
    assert result.action == "respond"
    assert result.trigger == TriggerKind.ALIAS_DIRECT
    assert result.decision_model_called is False


@pytest.mark.asyncio
async def test_model_error_defaults_to_silence():
    result = await DecisionEvaluator(RaisingDecisionModel(), GroupPolicy()).evaluate(
        ordinary_case()
    )
    assert result.action == "ignore"
    assert result.error_code == "decision_error"
```

- [ ] **步骤 2：确认测试失败**

运行：`python3 -m pytest tests/test_evaluation_replay.py -q`

预期：因 `DecisionEvaluator` 不存在而失败。

- [ ] **步骤 3：实现回放**

`DecisionEvaluator` 必须：

- 把场景消息依次加入 `TopicWindow`；
- 使用最新消息调用 `TriggerRouter`；
- 对 `IGNORE`、`COMMAND`、`NATIVE_DIRECT`、`ALIAS_DIRECT` 确定性处理；
- 只对 `ALIAS_MENTION` 和 `CANDIDATE` 调用 `DecisionModelPort`；
- 应用 `decision_threshold`；
- 捕获模型异常并安全沉默；
- 使用 `time.perf_counter_ns()` 记录耗时；
- 不导入 AstrBot，不调用生成、视觉或平台发送。

- [ ] **步骤 4：运行测试并提交**

运行：`python3 -m pytest tests/test_evaluation_replay.py -q`

提交：`feat: replay group participation decisions`

## 任务 3：指标、报告和命令行

**文件：**

- 创建 `groupmate/evaluation/metrics.py`
- 创建 `groupmate/evaluation/report.py`
- 创建 `groupmate/evaluation/cli.py`
- 创建 `tests/test_evaluation_metrics.py`
- 创建 `tests/test_evaluation_cli.py`

- [ ] **步骤 1：编写指标失败测试**

使用固定混淆矩阵验证：

```python
def test_may_respond_is_excluded_from_strict_metrics():
    report = calculate_metrics(cases_with_one_optional(), predictions())
    assert report.strict_sample_count == 2
    assert report.optional_sample_count == 1


def test_small_dataset_is_marked_insufficient():
    report = calculate_metrics(cases_with_one_optional(), predictions())
    assert report.sample_sufficient is False
```

- [ ] **步骤 2：确认指标测试失败**

运行：`python3 -m pytest tests/test_evaluation_metrics.py -q`

- [ ] **步骤 3：实现指标与稳定报告**

计算直接唤醒召回率、原生唤醒旁路率、指令旁路率、主动介入精确率、主动介入召回率、错误插话率、沉默准确率、结构成功率和 P50/P95 耗时。分母为零时返回 `null`，不得伪造 100%。严格样本少于 100 时 `sample_sufficient=false`。

JSON 使用 `ensure_ascii=False, sort_keys=True, indent=2`；Markdown 使用中文标题并明确展示“样本不足”。

- [ ] **步骤 4：编写 CLI 失败测试**

`validate` 校验数据集；`run` 使用安全沉默基线模型生成 `result.json` 和 `report.md`；`compare` 仅在数据集哈希相同时生成差异报告，否则返回非零。

- [ ] **步骤 5：确认 CLI 测试失败**

运行：`python3 -m pytest tests/test_evaluation_cli.py -q`

- [ ] **步骤 6：实现 CLI 并运行测试**

运行：

```bash
python3 -m pytest tests/test_evaluation_metrics.py tests/test_evaluation_cli.py -q
```

提交：`feat: report decision evaluation metrics`

## 任务 4：内置黄金场景

**文件：**

- 创建 `tests/fixtures/evaluation/golden.jsonl`
- 创建 `tests/fixtures/evaluation/default.json`
- 创建 `tests/test_evaluation_golden.py`

- [ ] **步骤 1：先编写场景数量和覆盖失败测试**

```python
def test_golden_dataset_has_required_coverage():
    dataset = load_dataset(GOLDEN_PATH)
    assert len(dataset.cases) >= 30
    tags = {tag for case in dataset.cases for tag in case.tags}
    assert {"wake", "command", "silence", "ordinary"} <= tags
```

- [ ] **步骤 2：确认测试失败**

运行：`python3 -m pytest tests/test_evaluation_golden.py -q`

- [ ] **步骤 3：创建 30 个虚构身份场景**

场景分布：

- 6 个别名直接唤醒；
- 5 个原生 `@` 或回复 Bot；
- 5 个既有指令；
- 5 个 Bot 自身、空内容或无效媒体；
- 5 个普通消息必须沉默场景；
- 4 个 `may_respond` 普通候选。

禁止使用真实 QQ 号、群号、昵称或学习素材原文。

- [ ] **步骤 4：验证 CLI 并提交**

运行：

```bash
python3 -m pytest tests/test_evaluation_golden.py -q
python3 -m groupmate.evaluation.cli validate --dataset tests/fixtures/evaluation/golden.jsonl
```

提交：`test: add built-in group decision scenarios`

## 任务 5：SQLite 影子记录和脱敏

**文件：**

- 修改 `groupmate/memory.py`
- 创建 `groupmate/evaluation/collector.py`
- 创建 `tests/test_shadow_storage.py`
- 创建 `tests/test_shadow_collector.py`

- [ ] **步骤 1：编写迁移和隐私失败测试**

验证数据库升级到版本 2、迁移可重复、记录幂等、默认上下文为空、过期清理和独立人工标签。

```python
def test_label_does_not_change_prediction(store):
    store.save_shadow_decision(shadow_record(action="ignore"))
    assert store.label_shadow_decision("d1", "must_respond", 20)
    row = store.get_shadow_decision("d1")
    assert row["action"] == "ignore"
    assert row["label"] == "must_respond"
```

- [ ] **步骤 2：确认测试失败**

运行：`python3 -m pytest tests/test_shadow_storage.py tests/test_shadow_collector.py -q`

- [ ] **步骤 3：实现存储和采集器**

`ShadowCollector` 只保留最近 20 条、最多 5 分钟窗口。非文本特征包含消息数、参与人数、文本长度、图片数、是否存在回复链。启用文本时使用“成员1、成员2”替代昵称和 ID，并丢弃 `metadata`、URL 与文件路径。

身份散列使用本地 32 字节随机盐和 `HMAC-SHA256`。盐通过原子创建保存在插件数据目录，权限允许时设置为 `0600`。

- [ ] **步骤 4：运行测试并提交**

运行：`python3 -m pytest tests/test_shadow_storage.py tests/test_shadow_collector.py -q`

提交：`feat: persist privacy-safe shadow decisions`

## 任务 6：零发送影子工作流

**文件：**

- 创建 `groupmate/evaluation/shadow.py`
- 修改 `groupmate/runtime.py`
- 创建 `tests/test_shadow_workflow.py`

- [ ] **步骤 1：编写零副作用失败测试**

```python
@pytest.mark.asyncio
async def test_shadow_workflow_never_generates_or_sends():
    workflow = build_shadow_workflow()
    outcome = await workflow.evaluate(topic(), TriggerKind.CANDIDATE, policy())
    assert outcome.sent is False
    assert fakes.generation.calls == 0
    assert fakes.vision.calls == 0
    assert fakes.platform.sent == []
    assert memory.shadow_count() == 1
```

同时验证 `COMMAND` 和 `NATIVE_DIRECT` 通过 Actor 的可选 `observe_bypass()` 被记录，而正式工作流行为不变。

- [ ] **步骤 2：确认测试失败**

运行：`python3 -m pytest tests/test_shadow_workflow.py tests/test_runtime.py -q`

- [ ] **步骤 3：实现 `ShadowWorkflow`**

`ShadowWorkflow` 只拥有决策模型、记忆、策略、采集器和时钟，不接受生成、视觉或平台端口。`evaluate()` 返回 `WorkflowOutcome(sent=False, reason="shadow_recorded")`。模型错误记录错误码并安全沉默。

`GroupActor` 只增加一个鸭子类型扩展：若工作流实现 `observe_bypass(topic, trigger, policy)`，则在 `IGNORE`、`COMMAND` 和 `NATIVE_DIRECT` 结束前调用；正式 `CognitiveWorkflow` 不实现该方法。

- [ ] **步骤 4：运行测试并提交**

运行：`python3 -m pytest tests/test_shadow_workflow.py tests/test_runtime.py -q`

提交：`feat: add zero-send shadow workflow`

## 任务 7：AstrBot 配置与管理员命令

**文件：**

- 修改 `groupmate/config.py`
- 修改 `_conf_schema.json`
- 修改 `groupmate/astrbot_adapter.py`
- 修改 `main.py`
- 修改 `README.md`
- 修改 `tests/test_config.py`
- 创建 `tests/test_shadow_bridge.py`

- [ ] **步骤 1：编写配置失败测试**

验证 `shadow_mode`、采样率、保留天数和文本保存配置的默认值与边界。

- [ ] **步骤 2：确认测试失败**

运行：`python3 -m pytest tests/test_config.py tests/test_shadow_bridge.py -q`

- [ ] **步骤 3：接入 Bridge**

`AstrBotBridge._workflow_for()` 在 `shadow_mode=true` 时构造 `ShadowWorkflow`，否则保持 `CognitiveWorkflow`。`status()` 返回影子模式和聚合数量。采样使用消息身份与群标识的稳定哈希，不使用全局随机状态。

- [ ] **步骤 4：增加管理员命令**

新增：

- `/groupmate_shadow_stats`：只返回数量、动作、标签和原因码聚合；
- `/groupmate_shadow_label <decision_id> <标签>`：接受中文标签映射，拒绝未知标签和不存在 ID。

README 用中文说明影子模式零发送、默认不存正文，以及开启正文后用于本地人工标注。

- [ ] **步骤 5：运行测试并提交**

运行：

```bash
python3 -m pytest tests/test_config.py tests/test_shadow_bridge.py -q
python3 -m json.tool _conf_schema.json >/dev/null
```

提交：`feat: expose shadow evaluation in AstrBot`

## 任务 8：完整验证与文档收尾

**文件：**

- 修改 `docs/superpowers/plans/2026-07-20-decision-evaluation-shadow-mode.md`，勾选完成项

- [ ] **步骤 1：运行全量测试**

```bash
python3 -m pytest -q
```

预期：全部通过，无残留异步任务警告。

- [ ] **步骤 2：运行编译与配置检查**

```bash
python3 -m compileall -q main.py groupmate tests
python3 -m json.tool _conf_schema.json >/dev/null
git diff --check
```

- [ ] **步骤 3：运行黄金数据端到端验证**

```bash
python3 -m groupmate.evaluation.cli validate \
  --dataset tests/fixtures/evaluation/golden.jsonl
python3 -m groupmate.evaluation.cli run \
  --dataset tests/fixtures/evaluation/golden.jsonl \
  --config tests/fixtures/evaluation/default.json \
  --output /tmp/groupmate-evaluation
test -f /tmp/groupmate-evaluation/result.json
test -f /tmp/groupmate-evaluation/report.md
```

- [ ] **步骤 4：检查隐私边界**

测试生成的影子记录与报告中不存在测试用原始 ID、OneBot metadata、资源 URL 或文件路径。

- [ ] **步骤 5：提交收尾**

提交：`docs: complete shadow evaluation rollout`
