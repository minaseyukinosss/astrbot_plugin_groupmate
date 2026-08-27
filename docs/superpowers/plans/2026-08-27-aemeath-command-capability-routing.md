# 爱弥斯命令纠错与能力路由实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让精确命令继续只返回命令结果，让唯一且低风险的错字命令先明确纠正再立即执行，并让自然语言能力调用同时服从注册目录、权限、风险和爱弥斯意愿。

**Architecture:** 在闲聊入口之前增加纯函数 `InteractionCommandResolver`，它只解析已注册命令，不从群消息猜测未知能力。精确外部命令继续由 AstrBot 原插件处理；错字自动执行只适用于注册了 `CommandExecutionPort` 的命令。自然语言能力使用现有 `CapabilityDescriptor`、`TaskRuntime` 和 `AstrBotCapabilityAdapter`，先走权限与确认，再由独立意愿策略决定 Persona-owned 能力。

**Tech Stack:** Python 数据类与 Enum、现有 AstrBot 事件桥、TaskRuntime、Provider Contract、pytest。

**Spec:** `docs/superpowers/specs/2026-08-27-aemeath-smalltalk-style-system-design.md`

## Global Constraints

- 本计划在 `2026-08-27-aemeath-smalltalk-style-core.md` 完成后执行。
- 精确命令不生成寒暄、Persona 补充或闲聊收尾。
- 自动纠错必须候选唯一、编辑差异小、参数无歧义、权限已满足且无需额外确认。
- 未注册执行端口的外部命令只能提示正确用法，不能伪装成已执行。
- 高好感不能绕过权限和确认；低好感不能阻止 `SAFETY_REQUIRED` 最低帮助。
- 能力执行结果必须是已验证 ProviderEvent，Bot 文本不能被当成执行回执。

---

## 文件职责

- 新建 `groupmate/social_runtime/interaction_commands.py`：注册命令描述、唯一纠错解析和执行端口契约。
- 新建 `groupmate/social_runtime/capability_willingness.py`：能力所有权分类与权限后意愿策略。
- 修改 `groupmate/social_runtime/tasks/contracts.py`：为能力描述增加向后兼容的所有权类别。
- 修改 `groupmate/adapters/astrbot_capabilities.py`：公开可自主、可人格拒绝和安全必需的目录视图。
- 修改 `groupmate/adapters/astrbot_bridge.py`：命令纠错先于闲聊，能力结果返回回复链。
- 修改 `main.py`：在现有精确命令处理后消费纠错结果。
- 修改 `groupmate/social_runtime/replying.py`：只注入已验证能力事实。
- 修改 `groupmate/social_runtime/control/message_traces.py`：记录纠错、权限、意愿和执行结果。
- 新建 `tests/social_runtime/test_interaction_commands.py`、`test_capability_willingness.py`。
- 修改 `tests/contracts/test_capability_provider.py`、`tests/scenarios/test_chat_mainline.py`、`tests/scenarios/test_task_topic_change.py`。

### Task 1: 建立注册命令目录和唯一纠错解析器

**Files:**
- Create: `groupmate/social_runtime/interaction_commands.py`
- Create: `tests/social_runtime/test_interaction_commands.py`

**Interfaces:**
- Consumes: 管理员或插件注册的 `InteractionCommandDescriptor`。
- Produces: `InteractionCommandResolver.resolve(text) -> CommandResolution`。

- [ ] **Step 1: 写入失败的精确、唯一和含糊解析测试**

```python
def test_exact_command_is_handed_off_without_social_text():
    result = _resolver("bq", "xw").resolve("bq 开心")
    assert result.kind is CommandResolutionKind.EXACT
    assert result.corrected_text is None
    assert result.args == "开心"


def test_unique_low_risk_typo_is_executable():
    result = _resolver("bq", "xw").resolve("bp 开心")
    assert result.kind is CommandResolutionKind.TYPO_EXECUTE
    assert result.corrected_text == "bq 开心"
    assert result.notice == "`bp` 打错啦，是 `bq`。"


def test_ambiguous_or_confirmation_command_only_suggests():
    ambiguous = _resolver("bq", "bp").resolve("bqg 开心")
    risky = _resolver("del", risk="HIGH_RISK").resolve("dek 1")
    assert ambiguous.kind is CommandResolutionKind.TYPO_SUGGEST
    assert risky.kind is CommandResolutionKind.TYPO_SUGGEST
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_interaction_commands.py`

Expected: FAIL during import.

- [ ] **Step 3: 实现严格目录和有界 Damerau-Levenshtein 距离**

```python
@dataclass(frozen=True)
class InteractionCommandDescriptor:
    command: str
    aliases: tuple[str, ...]
    owner_ref: str
    risk: str
    required_scopes: tuple[str, ...]
    requires_confirmation: bool
    executor_id: str | None


@dataclass(frozen=True)
class CommandResolution:
    kind: CommandResolutionKind
    descriptor: InteractionCommandDescriptor | None
    typed_command: str | None
    args: str
    corrected_text: str | None
    notice: str | None
    diagnostic_code: str
```

解析器仅比较第一个命令 token，最大距离为 1，保留参数原文。只有唯一候选、`executor_id` 非空、非 HIGH_RISK、无需确认且调用方传入的 scopes 覆盖要求时返回 `TYPO_EXECUTE`；否则返回 `TYPO_SUGGEST`。精确匹配返回 `EXACT`，普通文本返回 `NONE`。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_interaction_commands.py`

Expected: PASS.

- [ ] **Step 5: 提交命令解析器**

```bash
git add groupmate/social_runtime/interaction_commands.py tests/social_runtime/test_interaction_commands.py
git commit -m "feat: resolve registered command typos"
```

### Task 2: 给能力目录增加系统、人设、安全和高风险分类

**Files:**
- Modify: `groupmate/social_runtime/tasks/contracts.py`
- Create: `groupmate/social_runtime/capability_willingness.py`
- Modify: `groupmate/adapters/astrbot_capabilities.py`
- Create: `tests/social_runtime/test_capability_willingness.py`
- Modify: `tests/contracts/test_capability_provider.py`

**Interfaces:**
- Consumes: `CapabilityDescriptor`, `PermissionSnapshot`, `StanceDecision`。
- Produces: `CapabilityOwnership`, `CapabilityWillingnessDecision`, 分类型目录。

- [ ] **Step 1: 写入失败的权限与意愿矩阵**

```python
@pytest.mark.parametrize(
    ("ownership", "permission", "willingness", "allowed"),
    (
        ("SYSTEM_OWNED", True, "UNWILLING", True),
        ("PERSONA_OWNED", True, "UNWILLING", False),
        ("PERSONA_OWNED", True, "WILLING", True),
        ("SAFETY_REQUIRED", True, "UNWILLING", True),
        ("HIGH_RISK", False, "EAGER", False),
    ),
)
def test_permission_and_willingness_are_separate(ownership, permission, willingness, allowed):
    result = CapabilityWillingnessPolicy().decide(
        _descriptor(ownership=ownership),
        PermissionSnapshot(permission, "test"),
        _stance(willingness),
    )
    assert result.allowed is allowed
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/social_runtime/test_capability_willingness.py tests/contracts/test_capability_provider.py`

Expected: FAIL because descriptor ownership is absent.

- [ ] **Step 3: 实现向后兼容分类和目录过滤**

```python
class CapabilityOwnership(str, Enum):
    SYSTEM_OWNED = "SYSTEM_OWNED"
    PERSONA_OWNED = "PERSONA_OWNED"
    SAFETY_REQUIRED = "SAFETY_REQUIRED"
    HIGH_RISK = "HIGH_RISK"


@dataclass(frozen=True)
class CapabilityWillingnessDecision:
    allowed: bool
    reason_code: str
```

`CapabilityDescriptor` 在 `confirmation_policy` 后增加默认字段 `ownership: CapabilityOwnership = CapabilityOwnership.SYSTEM_OWNED`，保证现有直接构造仍可运行。`CapabilityDescriptor.create()` 在未提供 ownership 时根据风险向后兼容：READ_ONLY/LOW_IMPACT 默认 SYSTEM_OWNED，高风险默认 HIGH_RISK。`autonomous_catalog()` 仍要求注册、allowlist 和低风险；另加 `persona_catalog()` 与 `safety_catalog()`，但任何目录方法都不执行权限授权。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/social_runtime/test_capability_willingness.py tests/contracts/test_capability_provider.py tests/social_runtime/tasks/test_task_runtime.py`

Expected: PASS;旧 descriptor 测试无需全部重写。

- [ ] **Step 5: 提交能力分类**

```bash
git add groupmate/social_runtime/tasks/contracts.py groupmate/social_runtime/capability_willingness.py groupmate/adapters/astrbot_capabilities.py tests/social_runtime/test_capability_willingness.py tests/contracts/test_capability_provider.py
git commit -m "feat: separate capability permission and willingness"
```

### Task 3: 在 AstrBot 消息入口纠正并执行已注册命令

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `main.py`
- Modify: `groupmate/social_runtime/control/message_traces.py`
- Modify: `tests/scenarios/test_chat_mainline.py`
- Modify: `tests/contracts/test_message_traces.py`

**Interfaces:**
- Consumes: `InteractionCommandResolver`, `CommandExecutionPort`。
- Produces: `prepare_corrected_command(event) -> CorrectedCommandResult | None`。

- [ ] **Step 1: 写入失败的入口场景测试**

```python
def test_unique_typo_announces_then_executes_without_smalltalk(tmp_path):
    context, result, trace = asyncio.run(_run_typo_case(tmp_path, "bp 开心"))
    assert result.parts == ("`bp` 打错啦，是 `bq`。", "[表情结果]")
    assert context.reply_model_calls == []
    assert trace["command"]["resolution"] == "TYPO_EXECUTE"
    assert trace["route"]["owner"] == "EXTERNAL_PLUGIN"


def test_unregistered_external_typo_only_suggests(tmp_path):
    _, result, _ = asyncio.run(_run_typo_case(tmp_path, "xv 关键词", executable=False))
    assert result.parts == ("`xv` 打错啦，正确命令是 `xw`。",)
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/scenarios/test_chat_mainline.py -k 'typo' tests/contracts/test_message_traces.py -k 'command'`

Expected: FAIL because typo messages currently fall through to social handling.

- [ ] **Step 3: 实现执行端口和入口顺序**

```python
class CommandExecutionPort(Protocol):
    async def execute_corrected(
        self, descriptor: InteractionCommandDescriptor,
        *, args: str, source_event: object,
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class CorrectedCommandResult:
    parts: tuple[str, ...]
    resolution: CommandResolution
```

`main.py.observe_group_message()` 在精确 profile/affection 命令之后、`bridge.handle_event()` 之前调用 `prepare_corrected_command()`。命中时 `event.stop_event()`，按 parts 顺序 yield；不得继续调用闲聊模型。执行异常返回纠错提示加确定性失败说明，不伪造命令结果。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/scenarios/test_chat_mainline.py -k 'command or typo' tests/contracts/test_message_traces.py`

Expected: PASS;精确命令行为不变，错字只产生纠错和真实执行结果。

- [ ] **Step 5: 提交命令入口**

```bash
git add groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py main.py tests/scenarios/test_chat_mainline.py tests/contracts/test_message_traces.py
git commit -m "feat: correct and dispatch safe command typos"
```

### Task 4: 将自然语言能力请求接入 TaskRuntime

**Files:**
- Modify: `groupmate/adapters/astrbot_bridge.py`
- Modify: `groupmate/social_runtime/social_moves.py`
- Modify: `groupmate/social_runtime/replying.py`
- Modify: `tests/scenarios/test_task_topic_change.py`
- Modify: `tests/social_runtime/actions/test_replying.py`

**Interfaces:**
- Consumes: `SocialScene.capability_request`, `AstrBotCapabilityAdapter` catalog，权限 scopes，`CapabilityWillingnessPolicy`。
- Produces: `CapabilityRequest`, `TaskRun`, `VerifiedCapabilityFact` 或明确拒绝/失败的 `DecisionFact`。

- [ ] **Step 1: 写入失败的自然语言能力场景**

```python
def test_natural_weather_request_executes_only_registered_low_risk_capability(tmp_path):
    result = asyncio.run(_run_capability_case(tmp_path, "帮我看看上海天气"))
    assert result.task.status is TaskStatus.SUCCEEDED
    assert result.reply_plan.verified_capability_results[0].result_id == result.provider_event.event_id
    assert "晴" in result.sent_text


def test_persona_owned_capability_can_be_refused_without_revoking_permission(tmp_path):
    result = asyncio.run(_run_capability_case(tmp_path, "给我唱一段", affection=-60))
    assert result.permission.allowed is True
    assert result.willingness.allowed is False
    assert result.provider_calls == 0
    assert result.reply_plan.move.primary_move is SocialMove.REFUSE
```

- [ ] **Step 2: 运行测试并确认红灯**

Run: `pytest -q tests/scenarios/test_task_topic_change.py -k 'natural or persona_owned' tests/social_runtime/actions/test_replying.py -k 'capability'`

Expected: FAIL because catalog and TaskRuntime are not connected to chat planning.

- [ ] **Step 3: 实现权限后意愿、任务执行和结果回注**

解析出的 capability ID 必须精确存在于当前 catalog；不存在时生成“没有这个能力/正确命令”的 fact。对注册能力：调用 `CapabilityRequest.create` 构造请求，调用 `manager.task_runtime.propose`，按现有确认状态机 start/confirm，并通过 adapter 获取严格 `ProviderEvent`。成功结果转换为 `VerifiedCapabilityFact`；失败状态转换为失败 fact。`ReplyExecutor` 的 `GenerationRequest.verified_capability_results` 不再固定为空。

- [ ] **Step 4: 运行测试并确认绿灯**

Run: `pytest -q tests/scenarios/test_task_topic_change.py tests/social_runtime/tasks/test_task_runtime.py tests/contracts/test_capability_provider.py tests/social_runtime/actions/test_replying.py`

Expected: PASS;未验证文本不能成为成功结果，高风险请求停在确认状态。

- [ ] **Step 5: 提交自然语言能力接入**

```bash
git add groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/social_moves.py groupmate/social_runtime/replying.py tests/scenarios/test_task_topic_change.py tests/social_runtime/actions/test_replying.py
git commit -m "feat: invoke verified capabilities from smalltalk"
```

### Task 5: 完成命令与能力回归验收

**Files:**
- Modify: `tests/scenarios/test_chat_mainline.py`
- Modify: `tests/scenarios/test_task_topic_change.py`
- Modify: `tests/recovery/test_task_recovery.py`

**Interfaces:**
- Consumes: 完整命令与能力链。
- Produces: 精确命令、纠错、权限、意愿、确认、恢复的验收矩阵。

- [ ] **Step 1: 增加组合矩阵**

```python
@pytest.mark.parametrize(
    ("case", "model_calls", "provider_calls", "outcome"),
    (
        ("exact_bq", 0, 0, "HOST_HANDOFF"),
        ("unique_typo_bq", 0, 1, "TYPO_EXECUTE"),
        ("ambiguous_typo", 0, 0, "TYPO_SUGGEST"),
        ("high_risk_typo", 0, 0, "TYPO_SUGGEST"),
        ("natural_read_only", 2, 1, "SUCCEEDED"),
        ("persona_owned_refused", 2, 0, "REFUSED_BY_PERSONA"),
        ("safety_required_low_affection", 2, 1, "SUCCEEDED"),
        ("high_risk_natural", 2, 0, "AWAITING_CONFIRMATION"),
    ),
)
def test_command_capability_matrix(case, model_calls, provider_calls, outcome, tmp_path):
    result = asyncio.run(run_command_case(tmp_path, case))
    assert result.model_calls == model_calls
    assert result.provider_calls == provider_calls
    assert result.outcome == outcome
```

- [ ] **Step 2: 运行矩阵并确认红灯或现有遗漏**

Run: `pytest -q tests/scenarios/test_chat_mainline.py tests/scenarios/test_task_topic_change.py tests/recovery/test_task_recovery.py`

Expected: any missing integration fails with a specific case name.

- [ ] **Step 3: 只修复矩阵暴露的路由或恢复缺口**

确保纠错执行使用源事件 correlation 派生幂等键；ProviderEvent 重放不重复发送；确认和权限失败不调用 provider；外部精确命令不打开 conversation lease。

- [ ] **Step 4: 运行完整定向套件**

Run: `pytest -q tests/social_runtime/test_interaction_commands.py tests/social_runtime/test_capability_willingness.py tests/contracts/test_capability_provider.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_task_topic_change.py tests/recovery/test_task_recovery.py`

Expected: PASS. Also run `git diff --check`; expected no output.

- [ ] **Step 5: 提交验收补丁**

```bash
git add groupmate/social_runtime/interaction_commands.py groupmate/social_runtime/capability_willingness.py groupmate/social_runtime/tasks/contracts.py groupmate/social_runtime/social_moves.py groupmate/social_runtime/replying.py groupmate/adapters/astrbot_capabilities.py groupmate/adapters/astrbot_bridge.py groupmate/social_runtime/control/message_traces.py main.py tests/social_runtime/test_interaction_commands.py tests/social_runtime/test_capability_willingness.py tests/contracts/test_capability_provider.py tests/contracts/test_message_traces.py tests/scenarios/test_chat_mainline.py tests/scenarios/test_task_topic_change.py tests/recovery/test_task_recovery.py
git commit -m "test: verify command and capability routing"
```
