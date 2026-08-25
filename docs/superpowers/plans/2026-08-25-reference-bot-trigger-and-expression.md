# Groupmate 触发机制与人格表达实施计划

> **供执行代理使用：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项执行本计划。所有步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 让已经确认的 Persona 主名称与别称进入确定性的直接互动通道；保留 AstrBot 插件的能力归属；通过有界对话租约承接短程交流；生成符合当前 Persona 的群聊短回复，同时不照抄参考 Bot 的具体话术。

**架构：** 在平台事件翻译与 Social Runtime 接收之间加入纯函数式的 `PersonaAddressResolver`。它生成不可变的称呼事实与移除别称后的剩余正文；现有外部触发策略在注意力调度前对剩余正文重新判断归属。直接互动和对话延续保持确定性，`ExpressionPlan` 使用 Persona 线索塑造已经获准的回复；只有未直接称呼、也未命中对话租约的消息才能进入环境认知。

**技术栈：** Python 3 数据类、asyncio、SQLite 运行时仓库、pytest，以及设置页面使用的原生 ES 模块。

---

## 文件职责

- 新建 `groupmate/social_runtime/addressing.py`：规范化 Persona 名称并解析直接称呼事实，不进行平台 I/O。
- 新建 `groupmate/social_runtime/expression.py`：定义冻结的表达计划契约和确定性规划输入。
- 修改 `groupmate/settings.py`：提供 Persona 主名称与确认别称的部署级回退配置。
- 修改 `_conf_schema.json`：在 AstrBot 插件配置中暴露主名称与确认别称。
- 修改 `groupmate/social_runtime/persona/profile.py`：接受有界别称列表，并兼容旧版已发布人格。
- 修改 `groupmate/adapters/astrbot_bridge.py`：用当前 Persona 身份丰富翻译后的事件，并使用去除别称后的正文重新判断外部归属。
- 修改 `groupmate/social_runtime/attention.py`：只消费已经解析的直接称呼事实，直接互动不进入环境认知。
- 修改 `groupmate/social_runtime/world.py`：携带自然续聊所需的最小额外租约证据。
- 修改 `groupmate/social_runtime/manager.py`：在可用回复产生后打开或推进增强后的对话租约。
- 修改 `groupmate/social_runtime/replying.py`：持久化 `ExpressionPlan`，并注入最终回复生成提示。
- 修改 `groupmate/social_runtime/control/message_traces.py`：记录人类可读的称呼、归属、租约和表达证据。
- 修改 `pages/settings/components/presenters.js` 与 `pages/settings/components/inspector.js`：优先展示最终结果与触发依据，再展示技术诊断。
- 修改 `tests/social_runtime`、`tests/contracts`、`tests/scenarios`、`tests/shared` 和 `tests/page` 下的定向测试。

### 任务 1：增加向后兼容的 Persona 身份配置

**文件：**
- 修改：`_conf_schema.json`
- 修改：`groupmate/settings.py`
- 修改：`groupmate/social_runtime/persona/profile.py`
- 测试：`tests/shared/test_plugin_skeleton.py`
- 测试：`tests/social_runtime/test_persona_profile.py`

- [ ] **步骤 1：编写失败的设置与人格测试**

```python
def test_persona_identity_settings_normalize_confirmed_aliases():
    settings = SocialRuntimeSettings.from_mapping({
        "persona_name": " 爱弥斯 ",
        "persona_aliases": [" 小爱 ", "爱弥斯", "小爱", ""],
    })
    assert settings.persona_name == "爱弥斯"
    assert settings.persona_aliases == ("小爱",)


def test_old_persona_profile_without_aliases_remains_valid():
    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["identity"].pop("aliases")
    restored = GroupmatePersonaProfile.from_mapping(payload).to_mapping()
    assert restored["identity"]["aliases"] == []


def test_persona_profile_rejects_ambiguous_aliases():
    payload = GroupmatePersonaProfile.default().to_mapping()
    payload["identity"]["name"] = "爱弥斯"
    payload["identity"]["aliases"] = ["爱弥斯", "爱"]
    with pytest.raises(ValueError, match="persona alias"):
        GroupmatePersonaProfile.from_mapping(payload)
```

- [ ] **步骤 2：运行定向测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/shared/test_plugin_skeleton.py::test_astrbot_config_only_exposes_groupmate_deployment_choices tests/social_runtime/test_persona_profile.py
```

预期：测试失败，因为 `persona_name`、`persona_aliases` 和 `identity.aliases` 尚不存在。

- [ ] **步骤 3：实现有界身份配置**

增加部署配置字段：

```python
@dataclass(frozen=True)
class SocialRuntimeSettings:
    # 其余现有字段保持不变
    persona_name: str = "Groupmate"
    persona_aliases: tuple[str, ...] = ()

    @staticmethod
    def _persona_aliases(name: str, values: object) -> tuple[str, ...]:
        source = values if isinstance(values, (list, tuple)) else ()
        normalized = tuple(dict.fromkeys(
            str(value or "").strip() for value in source
            if str(value or "").strip()
        ))
        aliases = tuple(value for value in normalized if value != name)
        if len(aliases) > 12 or any(len(value) < 2 or len(value) > 24 for value in aliases):
            raise ValueError("persona aliases must contain 2-24 characters and at most 12 entries")
        return aliases
```

在 `from_mapping` 中先规范化主名称，再将其传给 `_persona_aliases`。增加以下配置模式：

```json
"persona_name": {
  "description": "Groupmate 人格名称",
  "type": "string",
  "default": "Groupmate",
  "hint": "用于自称和直接呼唤识别。"
},
"persona_aliases": {
  "description": "已确认的人格别称",
  "type": "list",
  "default": [],
  "hint": "每行一个别称。只有管理员确认的别称会触发直接互动。"
}
```

调整 `GroupmatePersonaProfile.sections`，允许存放有界别称元组，并与普通字符串字段分开规范化：

```python
@staticmethod
def _aliases(raw_section: Mapping[str, object], primary_name: str) -> tuple[str, ...]:
    raw = raw_section.get("aliases", ())
    if not isinstance(raw, (list, tuple)):
        raise ValueError("persona aliases must be a list")
    values = tuple(str(value or "").strip() for value in raw)
    if any(not value for value in values):
        raise ValueError("persona alias must not be empty")
    if len(set(values)) != len(values):
        raise ValueError("persona alias must be unique")
    if len(values) > 12 or any(len(value) < 2 or len(value) > 24 for value in values):
        raise ValueError("persona alias must contain 2-24 characters and at most 12 entries")
    if primary_name in values:
        raise ValueError("persona alias must differ from the primary name")
    return values
```

为保持向后兼容，输入中的 `aliases` 仍为可选字段；在冻结的身份段中以元组保存，并在 `to_mapping()` 中序列化为 `list(identity["aliases"])`。

- [ ] **步骤 4：运行测试并确认进入绿灯阶段**

运行：

```bash
pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py
```

预期：全部测试通过；配置模式暴露两个新的 Persona 字段，旧版已发布人格仍能正常加载。

- [ ] **步骤 5：提交变更**

```bash
git add _conf_schema.json groupmate/settings.py groupmate/social_runtime/persona/profile.py tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py
git commit -m "feat: configure persona names and aliases"
```

### 任务 2：将直接称呼解析为不可变事实

**文件：**
- 新建：`groupmate/social_runtime/addressing.py`
- 测试：`tests/social_runtime/test_addressing.py`

- [ ] **步骤 1：编写失败的解析器测试**

```python
@pytest.mark.parametrize(
    ("text", "addressed", "kind", "remainder"),
    (
        ("小爱", True, "PURE_ALIAS", ""),
        ("小爱呢", True, "ALIAS_PREFIX", "呢"),
        ("小爱 说话", True, "ALIAS_PREFIX", "说话"),
        ("爱弥斯 bq 开心", True, "ALIAS_PREFIX", "bq 开心"),
        ("你怎么看，小爱", True, "ALIAS_SUFFIX", "你怎么看"),
        ("我觉得小爱这个名字不错", False, "NONE", "我觉得小爱这个名字不错"),
        ("这是小爱情节", False, "NONE", "这是小爱情节"),
    ),
)
def test_resolver_distinguishes_calls_from_body_mentions(text, addressed, kind, remainder):
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text(text)
    assert result.addressed_to_bot is addressed
    assert result.address_kind == kind
    assert result.address_remainder == remainder


def test_platform_at_and_reply_are_high_confidence_without_alias_text():
    resolver = PersonaAddressResolver("爱弥斯", ("小爱",))
    at = resolver.resolve(text="早", mentions_bot=True, reply_to_bot=False)
    reply = resolver.resolve(text="然后呢", mentions_bot=False, reply_to_bot=True)
    assert (at.address_kind, reply.address_kind) == ("AT", "REPLY")
    assert at.address_confidence == reply.address_confidence == "HIGH"


def test_new_alias_is_only_recorded_as_a_candidate():
    result = PersonaAddressResolver("爱弥斯", ("小爱",)).resolve_text(
        "小爱以后叫你爱酱"
    )
    assert result.addressed_to_bot is True
    assert result.alias_candidate == "爱酱"
    assert "爱酱" not in PersonaAddressResolver("爱弥斯", ("小爱",)).names
```

- [ ] **步骤 2：运行解析器测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/social_runtime/test_addressing.py
```

预期：导入失败，因为 `PersonaAddressResolver` 尚未实现。

- [ ] **步骤 3：实现纯函数式解析器**

```python
@dataclass(frozen=True)
class AddressResolution:
    addressed_to_bot: bool
    address_kind: str
    matched_alias: str | None
    address_remainder: str
    address_confidence: str
    alias_candidate: str | None = None


class PersonaAddressResolver:
    def __init__(self, primary_name: str, aliases: tuple[str, ...] = ()) -> None:
        names = tuple(dict.fromkeys((primary_name.strip(), *(a.strip() for a in aliases))))
        if any(not value for value in names):
            raise ValueError("persona address names must not be empty")
        self.names = tuple(sorted(names, key=len, reverse=True))

    def resolve(self, *, text: str, mentions_bot: bool, reply_to_bot: bool) -> AddressResolution:
        if reply_to_bot:
            return AddressResolution(True, "REPLY", None, text.strip(), "HIGH")
        if mentions_bot:
            return AddressResolution(True, "AT", None, text.strip(), "HIGH")
        return self.resolve_text(text)

    def resolve_text(self, text: str) -> AddressResolution:
        value = " ".join(str(text or "").strip().split())
        for name in self.names:
            if value == name:
                return AddressResolution(True, "PURE_ALIAS", name, "", "HIGH")
            if value.startswith(name):
                remainder = value[len(name):].lstrip(" ，,。.!！?？~～…:：")
                if self._prefix_boundary(value[len(name):]):
                    return AddressResolution(
                        True,
                        "ALIAS_PREFIX",
                        name,
                        remainder,
                        "HIGH",
                        self._candidate(remainder),
                    )
            if value.endswith(name):
                leading = value[:-len(name)]
                if leading and leading[-1] in " ，,：:、":
                    remainder = leading.rstrip(" ，,：:、")
                    if remainder:
                        return AddressResolution(True, "ALIAS_SUFFIX", name, remainder, "HIGH")
        return AddressResolution(False, "NONE", None, value, "NONE", None)

    @staticmethod
    def _candidate(remainder: str) -> str | None:
        match = re.fullmatch(r"(?:以后)?叫你([\w\u3400-\u9fff·]{2,24})[。.!！?？~～]*", remainder)
        return match.group(1) if match else None
```

使用明确的标点或空白边界实现 `_prefix_boundary`，并只允许少量完整的口语承接词，例如 `呢、在、说、讲、帮、看、回、你、来、给、能、会、要、别`。禁止无边界子串匹配和模糊匹配。

- [ ] **步骤 4：运行解析器测试并确认进入绿灯阶段**

运行：

```bash
pytest -q tests/social_runtime/test_addressing.py
```

预期：全部解析器用例通过。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/social_runtime/addressing.py tests/social_runtime/test_addressing.py
git commit -m "feat: resolve persona direct addressing"
```

### 任务 3：移除别称后重新判断外部能力归属

**文件：**
- 修改：`groupmate/adapters/astrbot_bridge.py`
- 修改：`groupmate/adapters/astrbot_events.py`
- 测试：`tests/contracts/test_astrbot_events.py`
- 测试：`tests/scenarios/test_chat_mainline.py`

- [ ] **步骤 1：编写失败的桥接场景测试**

```python
async def _run_alias_case(tmp_path, text):
    context = _Context()
    settings = SocialRuntimeSettings.from_mapping({
        "enabled_groups": ["885617919"],
        "runtime_mode": "SOCIAL_RUNTIME",
        "generation_provider": "provider:text",
        "persona_name": "爱弥斯",
        "persona_aliases": ["小爱"],
        "external_command_prefixes": ["bq=astrbot.meme"],
    })
    bridge = AstrBotSocialRuntimeBridge(context, settings, tmp_path, clock=lambda: 100)
    await bridge.start()
    await bridge.handle_event(_event("alias-case", text))
    trace = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    await bridge.close()
    return context, trace


def test_alias_prefixed_external_command_stays_owned_by_astrbot(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱 bq 开心"))
    assert context.model_calls == []
    assert trace["route"]["owner"] == "EXTERNAL_PLUGIN"
    assert trace["route"]["reason"] == "匹配已配置的外部触发规则"


def test_alias_prefixed_social_call_enters_direct_lane(tmp_path):
    context, trace = asyncio.run(_run_alias_case(tmp_path, "小爱说话"))
    assert trace["decision"]["participation_lane"] == "DIRECT_FAST"
    cognition_calls = [
        call for call in context.model_calls
        if "结构化群聊观察器" in call["system_prompt"]
    ]
    assert cognition_calls == []
    assert len(context.client.calls) == 1
```

复用 `tests/scenarios/test_chat_mainline.py` 中已有的 `_Context`、`_event`、桥接启动、事件流查询和关闭辅助方法；断言必须检查真实事件流与发件箱行为，而不是模拟解析器结果。

- [ ] **步骤 2：运行场景并确认处于红灯阶段**

运行：

```bash
pytest -q tests/scenarios/test_chat_mainline.py -k 'alias_prefixed'
```

预期：`小爱 bq 开心` 尚未被识别为外部能力，`小爱说话` 错误落入 AMBIENT。

- [ ] **步骤 3：在桥接层丰富事件事实**

在桥接对象中保存一个 `ExternalTriggerPolicy` 和 `ConfigVersionRepository`。新增 `_profile_snapshot(group_id)`，优先级如下：显式发布的群级 `persona_profile` 决定身份；若不存在，则将插件的 `persona_name` 与 `persona_aliases` 覆盖到默认人格上。事件翻译完成后，根据当前人格构建 `PersonaAddressResolver`，解析消息并对去除别称后的剩余正文重新分类：

```python
def _resolve_interaction(self, event: SocialEventEnvelope) -> SocialEventEnvelope:
    profile = self._profile_snapshot(str(event.group_id or "")).config["persona_profile"]
    identity = profile["identity"]
    resolver = PersonaAddressResolver(
        str(identity["name"]),
        tuple(identity.get("aliases", ())),
    )
    resolution = resolver.resolve(
        text=str(event.payload.get("text") or ""),
        mentions_bot=bool(event.payload.get("mentions_bot")),
        reply_to_bot=bool(event.payload.get("reply_to_bot")),
    )
    ownership = self._external_trigger_policy.classify(
        resolution.address_remainder
    )
    payload = dict(event.payload)
    payload.update(asdict(resolution))
    payload["direct_address"] = resolution.addressed_to_bot
    if ownership is not None:
        payload.update({
            "interaction_owner": ownership.owner.value,
            "social_eligible": ownership.social_eligible,
            "owner_ref": ownership.owner_ref,
            "ownership_source": "alias_stripped_" + ownership.source,
            "external_trigger_kind": ownership.trigger_kind,
            "external_trigger_value": ownership.trigger_value,
        })
    return SocialEventEnvelope.create(**{**event.to_dict(), "payload": payload})
```

在 `handle_event` 和 `observe_event` 中完成纯平台翻译后，立即调用 `_resolve_interaction`。`AstrBotEventTranslator` 继续负责平台事实和不带别称的直接外部触发；不得删除现有事实字段。

- [ ] **步骤 4：运行归属与主链定向测试**

运行：

```bash
pytest -q tests/contracts/test_astrbot_events.py tests/scenarios/test_chat_mainline.py -k 'external or alias_prefixed or live_chat'
```

预期：带别称的命令会被移交；带别称的社交呼唤进入 `DIRECT_FAST`；现有不带别称的触发规则保持不变。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/adapters/astrbot_bridge.py groupmate/adapters/astrbot_events.py tests/contracts/test_astrbot_events.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: route alias calls through ownership and direct chat"
```

### 任务 4：让直接称呼证据明确且不依赖模型

**文件：**
- 修改：`groupmate/social_runtime/attention.py`
- 修改：`groupmate/social_runtime/control/message_traces.py`
- 测试：`tests/scenarios/test_attention_windows.py`
- 测试：`tests/evaluation/test_shadow_review.py`

- [ ] **步骤 1：编写失败的策略与证据测试**

```python
def test_confirmed_alias_is_fast_without_ambient_worker():
    event = _message(1, 100, "u1")
    event = SocialEventEnvelope.create(**{
        **event.to_dict(),
        "payload": {
            **dict(event.payload),
            "direct_address": True,
            "address_kind": "ALIAS_PREFIX",
            "matched_alias": "小爱",
            "address_remainder": "说话",
        },
    })
    world = GroupWorldProjector().apply(GroupWorldProjector().empty(event.group_id), event)
    frame = AttentionScheduler().on_event(event, world, _persona(), now=100)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.requested_workers == ()
```

增加事件流断言：人类可读的结果原因应为 `命中人格别称：小爱`；持久化证据保留 `address_kind`，但不得保存无界的模型内部文本。

- [ ] **步骤 2：运行定向测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/scenarios/test_attention_windows.py -k alias tests/evaluation/test_shadow_review.py -k alias
```

预期：通道可能已经是 FAST，但事件流证据和别称专属原因仍然缺失。

- [ ] **步骤 3：持久化有界称呼证据**

保持 `_is_fast` 的确定性，并增加明确的诊断映射：

```python
def _direct_reason(event: SocialEventEnvelope) -> str:
    kind = str(event.payload.get("address_kind") or "")
    if kind == "AT":
        return "明确 @ 机器人"
    if kind == "REPLY":
        return "回复了 Bot 的上一条消息"
    alias = " ".join(str(event.payload.get("matched_alias") or "").split())[:24]
    if kind in {"PURE_ALIAS", "ALIAS_PREFIX", "ALIAS_SUFFIX"} and alias:
        return f"命中人格别称：{alias}"
    return "明确对 Bot 发起互动"
```

将 `address_kind`、有界 `matched_alias`、`addressed_to_bot`、`alias_candidate` 和原因投影到 `summary.route`/`summary.judgement`；直接互动通道不得把这些字段加入认知模型输入。`alias_candidate` 只是展示证据：只有管理员将其加入已发布 Persona 或插件配置后，它才能成为解析器的生效名称。

- [ ] **步骤 4：运行策略与事件流定向测试**

运行：

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/evaluation/test_shadow_review.py -k 'alias or direct'
```

预期：直接别称成为带可读证据的策略决策，并且不会产生环境认知模块诊断。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/social_runtime/attention.py groupmate/social_runtime/control/message_traces.py tests/scenarios/test_attention_windows.py tests/evaluation/test_shadow_review.py
git commit -m "feat: explain deterministic alias participation"
```

### 任务 5：增强有界对话租约

**文件：**
- 修改：`groupmate/social_runtime/world.py`
- 修改：`groupmate/social_runtime/manager.py`
- 修改：`groupmate/social_runtime/attention.py`
- 测试：`tests/scenarios/test_chat_mainline.py`
- 测试：`tests/scenarios/test_attention_windows.py`

- [ ] **步骤 1：编写失败的对话延续测试**

```python
from dataclasses import replace

from groupmate.social_runtime.world import ConversationLease


def test_same_member_followup_can_continue_from_last_bot_reply_even_after_topic_projection_moves():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    direct = _message(1, 100, "u1")
    world = projector.apply(projector.empty("885617919"), direct)
    world = replace(
        world,
        conversation_lease=ConversationLease(
            target_id="u1",
            topic_id="m1",
            source_plan_id="reply:1",
            opened_at=100,
            expires_at=300,
            remaining_turns=5,
        ),
    )
    followup = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u1",
        occurred_at=150,
        received_at=150,
        correlation_id="corr:m2",
        payload={"text": "然后呢"},
    ))
    world = projector.apply(world, followup)
    frame = scheduler.on_event(followup, world, _persona(), now=150)[0]
    assert frame.trigger_kind == "CONTINUATION"
    assert frame.focus_topic_ids == ("m2",)


def test_new_direct_caller_preempts_existing_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    world = replace(base, conversation_lease=ConversationLease(
        "u1", "m1", "reply:1", 100, 300, 5
    ))
    direct = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u2",
        occurred_at=101,
        received_at=101,
        correlation_id="corr:m2",
        payload={"text": "小爱说话", "direct_address": True},
    ))
    world = projector.apply(world, direct)
    frame = scheduler.on_event(direct, world, _persona(), now=101)[0]
    assert frame.trigger_kind == "FAST"
    assert frame.candidate_audiences == ("u2",)


def test_external_capability_never_advances_social_lease():
    projector = GroupWorldProjector()
    scheduler = AttentionScheduler()
    base = projector.apply(projector.empty("885617919"), _message(1, 100, "u1"))
    lease = ConversationLease("u1", "m1", "reply:1", 100, 300, 5)
    world = replace(base, conversation_lease=lease)
    external = SocialEventEnvelope.create(**social_event_values(
        event_id="qq:m2",
        source_message_id="m2",
        actor_id="u1",
        occurred_at=101,
        received_at=101,
        correlation_id="corr:m2",
        payload={"text": "bq 开心", "social_eligible": False},
    ))
    world = projector.apply(world, external)
    assert scheduler.on_event(external, world, _persona(), now=101) == ()
    assert world.conversation_lease == lease
```

使用真实的 `GroupWorldProjector`、`AttentionScheduler` 和桥接主链路，不要用桩对象替代对话租约。

- [ ] **步骤 2：运行延续测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py -k 'followup or preempts or capability_never_advances'
```

预期：话题投影发生变化后的追问无法续聊，增强后的证据字段尚不存在。

- [ ] **步骤 3：增加最小必要的续聊证据**

扩展冻结的对话租约：

```python
@dataclass(frozen=True)
class ConversationLease:
    target_id: str
    topic_id: str
    source_plan_id: str
    opened_at: int
    expires_at: int
    remaining_turns: int
    last_bot_event_id: str | None = None
    unresolved_intent: str | None = None
    last_activity_at: int | None = None
```

在 `record_usable_reply` 中填充这些字段。按照以下顺序更新 `_matches_conversation_lease`：

```python
if event.payload.get("social_eligible") is False:
    return False
if event.payload.get("direct_address"):
    return False  # FAST has already won and may change the target
if event.actor_id != lease.target_id or now > lease.expires_at or lease.remaining_turns <= 0:
    return False
if event.payload.get("reply_to_bot"):
    return True
if AttentionScheduler._topic_id(world, event) == lease.topic_id:
    return True
return _looks_like_short_followup(str(event.payload.get("text") or ""))
```

将 `_looks_like_short_followup` 严格限制为 `然后呢、后来呢、为什么、怎么了、那怎么办、继续、还有呢` 等对话承接语和简短回答片段。该方法不得调用模型，也不得把同一成员发送的任意消息都视为续聊。

- [ ] **步骤 4：运行租约定向测试**

运行：

```bash
pytest -q tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py -k 'lease or continuation or followup or preempts'
```

预期：有效追问能够续聊；新的直接呼唤可以抢占；外部能力不会改变对话租约。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/social_runtime/world.py groupmate/social_runtime/manager.py groupmate/social_runtime/attention.py tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py
git commit -m "feat: continue bounded member conversations"
```

### 任务 6：规划符合 Persona 的群聊表达

**文件：**
- 新建：`groupmate/social_runtime/expression.py`
- 修改：`groupmate/social_runtime/replying.py`
- 修改：`groupmate/social_runtime/manager.py`
- 修改：`groupmate/adapters/astrbot_bridge.py`
- 测试：`tests/social_runtime/actions/test_replying.py`
- 测试：`tests/social_runtime/test_expression.py`

- [ ] **步骤 1：编写失败的表达契约测试**

```python
def test_direct_expression_reacts_then_answers_and_may_leave_a_hook():
    profile = GroupmatePersonaProfile.default().to_mapping()
    profile["identity"]["name"] = "爱弥斯"
    plan = ExpressionPlanner().plan(
        lane="DIRECT_FAST",
        act="respond_to_direct_interaction",
        source_text="小爱你怎么不理我",
        persona_profile=profile,
    )
    assert plan.reaction_stance == "acknowledge_relationship"
    assert plan.core_response_goal == "respond_to_direct_interaction"
    assert plan.persona_cues[0] == "爱弥斯"
    assert plan.followup_hook == "optional_if_natural"
    assert plan.message_count in {1, 2}


def test_expression_uses_persona_cues_without_reference_bot_phrases():
    prompt = ReplyExecutor._system_prompt(reply_plan, persona_profile)
    assert "爱弥斯" in prompt
    assert "咪呀" not in prompt
    assert "花房" not in prompt
    assert "先接住对方的情绪和关系信号" in prompt
```

- [ ] **步骤 2：运行表达测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py -k expression
```

预期：`ExpressionPlan` 和 `ExpressionPlanner` 尚不存在，回复计划只能携带通用样式指令。

- [ ] **步骤 3：实现冻结的表达计划**

```python
@dataclass(frozen=True)
class ExpressionPlan:
    reaction_stance: str
    core_response_goal: str
    persona_cues: tuple[str, ...]
    boundary_style: str
    followup_hook: str
    message_count: int
    capability_request: str | None = None

    def __post_init__(self) -> None:
        if self.message_count not in {1, 2}:
            raise ValueError("expression message_count must be 1 or 2")
        if len(self.persona_cues) > 6:
            raise ValueError("expression persona cues must be bounded")


class ExpressionPlanner:
    _RELATION_WORDS = ("不理我", "不要我", "喜欢", "想你", "生气", "难过", "高兴", "谢谢")

    def plan(
        self,
        *,
        lane: str,
        act: str,
        source_text: str,
        persona_profile: Mapping[str, object],
    ) -> ExpressionPlan:
        identity = persona_profile.get("identity", {})
        expression = persona_profile.get("expression", {})
        participation = persona_profile.get("participation", {})
        name = str(identity.get("name") or "Groupmate").strip()[:24]
        cues = tuple(
            value for value in (
                name,
                str(identity.get("background") or "").strip()[:120],
                str(expression.get("tone") or "").strip()[:120],
                str(expression.get("language_habits") or "").strip()[:120],
            ) if value
        )
        relationship = any(word in source_text for word in self._RELATION_WORDS)
        reaction = (
            "acknowledge_relationship" if relationship
            else "continue_current_exchange" if lane == "CONTINUATION"
            else "attentive"
        )
        return ExpressionPlan(
            reaction_stance=reaction,
            core_response_goal=act,
            persona_cues=cues,
            boundary_style=str(participation.get("stay_silent_when") or "明确且友好")[:120],
            followup_hook=(
                "optional_if_natural"
                if lane in {"DIRECT_FAST", "CONTINUATION"}
                else "only_if_it_adds_value"
            ),
            message_count=2 if relationship else 1,
        )
```

`ExpressionPlanner.plan` 使用已经批准的通道、行为、原始文本和安全的 Persona 段落。对于直接表达情绪的内容，它设置能感知关系的反应方式；对于租约内回复，它设置承接对话的姿态；对于环境回复，它设置低打扰姿态。不得逐字复制参考数据中的任何话术。

向 `ReplyPlan` 增加 `expression: ExpressionPlan`。在 `ReplyPlanRepository._decode` 中，为旧版已存储计划创建保守的默认值。修改规划器契约，使其接收本次评估所使用的冻结 Persona 配置：

```python
def plan(
    self,
    evaluation: object,
    *,
    now: int,
    persona_profile: Mapping[str, object],
) -> ReplyPlan | None:
    frame = getattr(evaluation, "frame", None)
    governor = getattr(evaluation, "governor_result", None)
    if not getattr(evaluation, "accepted", False) or frame is None or governor is None:
        return None
    if governor.outcome != "ACT" or len(governor.selected_intention_ids) != 1:
        return None
    intention_id = governor.selected_intention_ids[0]
    selected = next(
        (item for item in getattr(evaluation, "candidates", ())
         if item.intention_id == intention_id),
        None,
    )
    if selected is None or selected.expires_at <= int(now):
        return None
    expression = self._expression_planner.plan(
        lane=str(getattr(evaluation, "participation_lane", "AMBIENT")),
        act=selected.proposed_act,
        source_text=str(evaluation.source_event.payload.get("text") or ""),
        persona_profile=persona_profile,
    )
    return self._build_plan(
        evaluation=evaluation,
        frame=frame,
        selected=selected,
        intention_id=intention_id,
        expression=expression,
        now=int(now),
    )
```

将当前 `ReplyPlan(...)` 的构造逻辑原样提取到 `_build_plan(...)`，只增加 `expression=expression`；身份、过期时间、目标、话题和样式的构造方式均保持不变。

在管理器中提供只读方法，仅当 `group_id` 和 `config_version` 与冻结评估一致时才返回 `profile.to_mapping()`。在 `_handle_evaluations` 中只获取一次该映射，并将其传给 `ReplyPlanner.plan`、`ReplyExecutor.preview` 和 `ReplyExecutor.execute_with_result`，替换当前的 `{"persona_id": ...}` 占位数据。这样可以防止触发判断所用身份与生成回复所用身份发生偏离。

将表达计划传给 `_system_prompt`，并加入以下明确指令：

```python
"按顺序组织：可选即时反应、核心回应、少量人格化补充、可选续聊接口。"
"先接住对方的情绪和关系信号，再处理事实；没有明显情绪时不要硬演。"
"拒绝时明确边界并给简短理由；技术回答给可能原因和一个可执行步骤。"
"只使用 Persona 中提供的自称、语气和背景，不模仿任何参考 Bot 的固定口癖。"
```

- [ ] **步骤 4：运行回复与表达测试**

运行：

```bash
pytest -q tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py
```

预期：表达计划能够持久化；旧回复计划仍可读取；提示中只包含当前生效的 Persona 线索。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/social_runtime/expression.py groupmate/social_runtime/replying.py groupmate/social_runtime/manager.py groupmate/adapters/astrbot_bridge.py tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py
git commit -m "feat: plan persona-specific group chat expression"
```

### 任务 7：优先展示触发机制，再展示技术诊断

**文件：**
- 修改：`groupmate/social_runtime/control/message_traces.py`
- 修改：`pages/settings/components/presenters.js`
- 修改：`pages/settings/components/inspector.js`
- 修改：`pages/settings/fixtures/fake_bridge.js`
- 测试：`tests/page/test_product_ui.py`
- 测试：`tests/page/test_shadow_console.py`

- [ ] **步骤 1：编写失败的展示层测试**

```python
def test_runtime_result_prefers_human_trigger_basis():
    result = _run_presenter(
        "const item={route:{owner:'GROUPMATE',address_kind:'ALIAS_PREFIX',"
        "matched_alias:'小爱'},decision:{outcome:'ACT',"
        "participation_lane:'DIRECT_FAST'}};"
        "console.log(JSON.stringify(presenter.traceResultReason(item)));"
    )
    assert result == "命中人格别称：小爱"


def test_continuation_result_does_not_say_generic_formal_runtime_will_not_reply():
    result = _run_presenter(
        "const item={decision:{outcome:'ACT',would_reply:true,"
        "participation_lane:'CONTINUATION'}};"
        "console.log(JSON.stringify(presenter.traceResultHeadline(item)));"
    )
    assert result == "继续当前对话"
```

复用 `tests/page/test_product_ui.py` 中现有的 Node 展示层测试工具，不要引入浏览器依赖。

- [ ] **步骤 2：运行页面测试并确认处于红灯阶段**

运行：

```bash
pytest -q tests/page/test_product_ui.py tests/page/test_shadow_console.py
```

预期：别称依据尚未展示，ACT 始终渲染成同一个通用标题。

- [ ] **步骤 3：实现简洁的结果优先文案**

更新展示层映射：

```javascript
export function traceResultHeadline(summary = {}) {
  if (String(summary.route?.owner || "").toUpperCase() === "EXTERNAL_PLUGIN") return "由外部能力处理";
  const decision = summary.decision || {};
  const outcome = String(decision.outcome || decision.pre_gate_outcome || "").toUpperCase();
  if (outcome === "ACT") {
    if (decision.participation_lane === "CONTINUATION") return "继续当前对话";
    if (decision.participation_lane === "DIRECT_FAST") return "会回应这次呼唤";
    return "适合加入当前话题";
  }
  if (!outcome || outcome === "PENDING") return "等待完成判断";
  if (String(summary.judgement?.status || "") === "unavailable") return "判断未完成";
  return ({
    OBSERVE: "本轮暂不参与",
    SILENCE: "本轮不回复",
    DEFER: "稍后重新判断",
  })[outcome] || "已完成判断";
}
```

让 `traceResultReason` 优先使用有界的称呼、租约和能力归属证据，其次使用模型判断，最后才使用治理原因。为 `record_plan` 增加安全的表达计划投影：

```python
expression = getattr(plan, "expression", None)
if expression is not None:
    summary["expression"] = {
        "reaction_stance": self._safe_text(expression.reaction_stance, 40),
        "core_response_goal": self._safe_text(expression.core_response_goal, 80),
        "followup_hook": self._safe_text(expression.followup_hook, 40),
        "message_count": max(1, min(2, int(expression.message_count))),
        "capability_request": self._safe_text(expression.capability_request, 60) or None,
    }
```

在详情检查器中，将 `触发方式、命中别称、能力归属、对话对象、租约状态、表达计划` 展示在折叠的技术信息之前。不得展示原始 ID、`persona_cues`、无界模型输出或 Persona 背景信息。

- [ ] **步骤 4：运行页面测试并确认进入绿灯阶段**

运行：

```bash
pytest -q tests/page/test_product_ui.py tests/page/test_shadow_console.py
```

预期：直接互动、对话延续、外部能力和环境观察具有清晰不同的展示结果，现有主题与布局契约继续通过。

- [ ] **步骤 5：提交变更**

```bash
git add groupmate/social_runtime/control/message_traces.py pages/settings/components/presenters.js pages/settings/components/inspector.js pages/settings/fixtures/fake_bridge.js tests/page/test_product_ui.py tests/page/test_shadow_console.py
git commit -m "feat: explain chat trigger and expression outcomes"
```

### 任务 8：使用小型场景矩阵验证完整机制

**文件：**
- 修改：`tests/scenarios/test_chat_mainline.py`
- 修改：`docs/operations/social-runtime-shadow.md`

- [ ] **步骤 1：增加端到端场景矩阵**

```python
from groupmate.adapters.deepseek_cognition import DirectCognitionResponse


class _MatrixCognition:
    model = "test-cognition"

    def __init__(self):
        self.calls = 0

    def input_bytes(self, facts):
        return len(json.dumps(facts, ensure_ascii=False).encode("utf-8"))

    async def classify(self, facts):
        self.calls += 1
        evidence = facts["events"][-1]["id"]
        return DirectCognitionResponse(
            verdict={
                "decision": "silence",
                "signal": "none",
                "target_id": None,
                "evidence_event_ids": [evidence],
                "confidence": 0.9,
                "disruption": 0.8,
                "novelty": 0.1,
                "reason": "普通正文提及，不构成直接呼唤",
            },
            latency_ms=1,
            request_bytes=1,
            backend="test",
            model=self.model,
        )

    async def close(self):
        return None


async def _trigger_case(tmp_path, message):
    context = _Context()
    cognition = _MatrixCognition()
    settings = SocialRuntimeSettings.from_mapping({
        "enabled_groups": ["885617919"],
        "runtime_mode": "SHADOW",
        "generation_provider": "provider:text",
        "cognition_api_key": "sk-test",
        "persona_name": "爱弥斯",
        "persona_aliases": ["小爱"],
        "external_command_prefixes": ["bq=astrbot.meme"],
    })
    bridge = AstrBotSocialRuntimeBridge(
        context,
        settings,
        tmp_path,
        clock=lambda: 100,
        cognition_client_factory=lambda _: cognition,
    )
    await bridge.start()
    await bridge.handle_event(_event("matrix", message))
    due = await bridge.manager.drain(now=102)
    await bridge._handle_evaluations(due)
    summary = bridge.trace_repository.query(
        persona_id=settings.persona_id,
        group_id="885617919",
    )["items"][0]["summary"]
    ambient_calls = cognition.calls
    await bridge.close()
    return summary, ambient_calls


@pytest.mark.parametrize(
    ("message", "expected_owner", "expected_lane", "ambient_calls"),
    (
        ("小爱", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱说话", "GROUPMATE", "DIRECT_FAST", 0),
        ("小爱 bq 开心", "EXTERNAL_PLUGIN", None, 0),
        ("我觉得小爱这个名字不错", "GROUPMATE", "AMBIENT", 1),
    ),
)
def test_persona_trigger_matrix(message, expected_owner, expected_lane, ambient_calls, tmp_path):
    summary, actual_ambient_calls = asyncio.run(_trigger_case(tmp_path, message))
    assert summary["route"]["owner"] == expected_owner
    assert summary["decision"].get("participation_lane") == expected_lane
    assert actual_ambient_calls == ambient_calls
```

增加第二个场景：直接呼唤 → 可用回复 → `然后呢` → 续聊回复 → 外部命令。断言共发送或预览两次回复、租约剩余轮次减少，并且外部命令不会推进租约。

- [ ] **步骤 2：运行场景矩阵**

运行：

```bash
pytest -q tests/scenarios/test_chat_mainline.py
```

预期：全部直接互动、外部能力、对话延续和环境观察场景通过，不产生意外模型调用。

- [ ] **步骤 3：记录 SHADOW 验证方法**

将以下运维复核清单加入 `docs/operations/social-runtime-shadow.md`：

```markdown
## Persona 触发机制复核

1. 分别发送主名称、确认别称、带别称的外部命令、普通正文提及和一次自然追问。
2. 运行中心应依次显示“会回应这次呼唤”“由外部能力处理”“普通群聊观察”“继续当前对话”。
3. 主名称、别称和续聊不得出现 `ambient_social_assessor`；普通正文提及可以出现。
4. SHADOW 只预览表达，不建立真实对话租约；正式运行仅在可用回复成功发送后建立租约。
5. 技术详情中不得出现 API Key、原始模型异常、内部提示词或原始成员 ID。
```

- [ ] **步骤 4：只运行相关回归测试集合**

运行：

```bash
pytest -q tests/shared/test_plugin_skeleton.py tests/social_runtime/test_persona_profile.py tests/social_runtime/test_addressing.py tests/social_runtime/test_expression.py tests/social_runtime/actions/test_replying.py tests/contracts/test_astrbot_events.py tests/scenarios/test_attention_windows.py tests/scenarios/test_chat_mainline.py tests/page/test_product_ui.py tests/page/test_shadow_console.py
git diff --check
```

预期：定向测试全部通过，`git diff --check` 不输出任何内容。除非定向测试暴露跨模块回归，否则不运行无关的完整测试套件。

- [ ] **步骤 5：提交变更**

```bash
git add tests/scenarios/test_chat_mainline.py docs/operations/social-runtime-shadow.md
git commit -m "test: verify persona trigger and continuation mechanism"
```
