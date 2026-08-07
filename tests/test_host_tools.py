import asyncio
import types

from groupmate.tools import (
    AstrBotToolPersonaRenderer,
    AstrBotToolPlanner,
    CapturingEventProxy,
    GroupmateToolOrchestrator,
    HostToolExecutor,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolPolicyEngine,
    ToolRisk,
    ToolSource,
    ToolPlan,
    ToolExecutionResult,
    UniversalToolCatalog,
    create_capturing_event,
)
from groupmate.models import ChatMessage, TriggerKind


class FakeEvent:
    def __init__(self, *, admin=False, group_role="member"):
        self._admin = admin
        self.role = group_role
        self.sent = []
        self.extras = {}
        self.message_obj = types.SimpleNamespace(raw_message={"sender": {"role": group_role}})

    def is_admin(self):
        return self._admin

    def get_sender_id(self):
        return "10001"

    async def send(self, message):
        self.sent.append(message)
        return True

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)


def descriptor(
    *,
    risk=ToolRisk.READ_ONLY,
    permission="member",
    source=ToolSource.LLM_TOOL,
    native=None,
    passthrough_send=False,
):
    return ToolDescriptor(
        tool_id="llm:test",
        name="test",
        description="测试工具",
        source=source,
        parameters={"type": "object", "properties": {}},
        risk=risk,
        permission=permission,
        passthrough_send=passthrough_send,
        native=native,
    )


def test_policy_requires_admin_for_dangerous_and_unknown_tools():
    policy = ToolPolicyEngine()

    denied = policy.authorize(
        descriptor(risk=ToolRisk.DANGEROUS),
        FakeEvent(),
    )
    assert denied.allowed is False
    assert denied.reason == "admin_required"

    assert policy.authorize(
        descriptor(risk=ToolRisk.UNKNOWN),
        FakeEvent(group_role="admin"),
    ).allowed
    assert policy.authorize(
        descriptor(risk=ToolRisk.DANGEROUS),
        FakeEvent(admin=True),
    ).allowed


def test_capturing_proxy_does_not_send_or_mutate_real_event():
    event = FakeEvent()
    proxy = CapturingEventProxy(event)

    asyncio.run(proxy.send("正在处理"))
    proxy.stop_event()
    proxy.should_call_llm(True)
    proxy.set_extra("parsed_params", {"days": 1})

    assert proxy.captured_outputs == ("正在处理",)
    assert event.sent == []
    assert event.extras == {}
    assert proxy.is_stopped() is True


def test_capturing_event_preserves_type_and_can_passthrough_media_send():
    class PlatformEvent(FakeEvent):
        async def send(self, message):
            self.sent.append(message)
            return None

    event = PlatformEvent()
    proxy = create_capturing_event(event, passthrough_send=True)

    sent = asyncio.run(proxy.send("歌曲卡片"))

    assert isinstance(proxy, PlatformEvent)
    assert sent is True
    assert proxy.direct_sent is True
    assert event.sent == ["歌曲卡片"]


def test_executor_calls_llm_tool_with_capturing_event():
    async def tool_handler(event, name):
        await event.send("内部进度")
        return "完成：" + name

    tool = types.SimpleNamespace(handler=tool_handler)
    result = asyncio.run(
        HostToolExecutor(types.SimpleNamespace()).execute(
            descriptor(native=tool),
            {"name": "群分析"},
            FakeEvent(),
        )
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.outputs == ("内部进度", "完成：群分析")


def test_executor_respects_command_filters():
    class PermissionFilter:
        def filter(self, event, config):
            del config
            return event.is_admin()

    class CommandFilter:
        handler_params = {"days": None}

        def custom_filter_ok(self, event, config):
            del event, config
            return True

    async def handler(event, days=None):
        del event
        return "分析天数：" + str(days)

    command_filter = CommandFilter()
    metadata = types.SimpleNamespace(
        handler=handler,
        event_filters=[command_filter, PermissionFilter()],
    )
    command = ToolDescriptor(
        tool_id="command:test",
        name="群分析",
        description="分析群聊",
        source=ToolSource.COMMAND,
        risk=ToolRisk.READ_ONLY,
        native=(metadata, command_filter),
    )
    executor = HostToolExecutor(types.SimpleNamespace(get_config=lambda: {}))

    denied = asyncio.run(executor.execute(command, {"days": 2}, FakeEvent()))
    allowed = asyncio.run(
        executor.execute(command, {"days": 2}, FakeEvent(admin=True))
    )

    assert denied.status is ToolExecutionStatus.DENIED
    assert allowed.status is ToolExecutionStatus.SUCCESS
    assert allowed.outputs == ("分析天数：2",)


def test_catalog_discovers_and_prioritizes_native_llm_tools():
    tool = types.SimpleNamespace(
        name="group_analysis",
        description="分析群聊日常活动（跨平台支持）",
        parameters={"type": "object", "properties": {"days": {"type": "integer"}}},
        active=True,
        handler_module_path="data.plugins.astrbot_plugin_analysis.main",
        is_background_task=False,
    )
    manager = types.SimpleNamespace(
        get_full_tool_set=lambda: types.SimpleNamespace(tools=[tool]),
        _default_permission=lambda name: "member",
    )
    catalog = UniversalToolCatalog(
        types.SimpleNamespace(get_llm_tool_manager=lambda: manager)
    )

    items = catalog.refresh()
    candidates = catalog.retrieve("小爱总结一下最近的群消息")

    assert len(items) == 1
    assert candidates[0].tool_id == "llm:group_analysis"
    assert candidates[0].risk is ToolRisk.READ_ONLY


def test_command_parameter_schema_uses_handler_signature_defaults():
    class CommandFilter:
        handler_params = {"days": None}

    async def handler(event, days: int | None = None):
        del event, days

    schema = __import__(
        "groupmate.tools.catalog",
        fromlist=["_command_parameters"],
    )._command_parameters(CommandFilter(), handler)

    assert schema["properties"]["days"]["type"] == "integer"
    assert "required" not in schema
    assert schema["properties"]["days"].get("default") is None


def test_executor_coerces_chinese_day_count_to_int():
    class CommandFilter:
        handler_params = {"days": None}

    async def handler(event, days: int | None = None):
        return f"days={days!r}"

    command = ToolDescriptor(
        tool_id="command:analysis",
        name="群分析",
        description="分析群聊",
        source=ToolSource.COMMAND,
        risk=ToolRisk.READ_ONLY,
        parameters={
            "type": "object",
            "properties": {"days": {"type": "integer", "default": None}},
        },
        native=(
            types.SimpleNamespace(handler=handler, event_filters=[]),
            CommandFilter(),
        ),
    )
    result = asyncio.run(
        HostToolExecutor(types.SimpleNamespace(get_config=lambda: {})).execute(
            command,
            {"days": "2天"},
            FakeEvent(admin=True),
        )
    )
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.outputs == ("days=2",)


def test_orchestrator_does_not_clarify_optional_days():
    selected = ToolDescriptor(
        tool_id="command:analysis",
        name="群分析",
        description="分析群聊 用法: /群分析 [天数]",
        source=ToolSource.COMMAND,
        parameters={
            "type": "object",
            "properties": {"days": {"type": "integer", "default": None}},
        },
        risk=ToolRisk.READ_ONLY,
        native=object(),
    )
    executed = []

    class Catalog:
        def refresh(self):
            return (selected,)

        def retrieve(self, message, limit):
            del message, limit
            return (selected,)

        def get(self, tool_id):
            return selected if tool_id == selected.tool_id else None

    class Planner:
        async def plan(self, **kwargs):
            del kwargs
            return ToolPlan(
                True,
                selected.tool_id,
                arguments={},
                missing_arguments=("days",),
            )

    class Renderer:
        async def clarification(self, *args):
            del args
            raise AssertionError("optional days must not clarify")

        async def progress(self, *args):
            del args
            return "稍等"

        async def final(self, *args):
            del args
            return "好了"

    class Executor:
        async def execute(self, descriptor, arguments, event):
            del descriptor, event
            executed.append(arguments)
            return ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                selected.tool_id,
                outputs=("分析完成",),
            )

    class RecordingOrchestrator(GroupmateToolOrchestrator):
        async def _deliver(self, actor, message, text, **kwargs):
            del actor, message, text, kwargs

    class Event(FakeEvent):
        def stop_event(self):
            pass

        def should_call_llm(self, value):
            del value

    actor = types.SimpleNamespace(
        router=types.SimpleNamespace(
            classify=lambda message: types.SimpleNamespace(
                kind=TriggerKind.ALIAS_DIRECT
            )
        ),
        window=types.SimpleNamespace(
            snapshot=lambda: types.SimpleNamespace(messages=())
        ),
    )
    orchestrator = RecordingOrchestrator(
        catalog=Catalog(),
        planner=Planner(),
        renderer=Renderer(),
        executor=Executor(),
        policy=types.SimpleNamespace(
            authorize=lambda descriptor, event: types.SimpleNamespace(allowed=True)
        ),
    )
    message = ChatMessage(
        "m1", "g1", "u1", "群友", "小爱分析一下群聊", 100
    )
    handled = asyncio.run(orchestrator.try_handle(Event(), actor, message))
    assert handled is True
    assert executed == [{}]


def test_catalog_retrieves_colloquial_music_and_rename_intents():
    music = ToolDescriptor(
        tool_id="llm:play_song_by_name",
        name="play_song_by_name",
        description="当用户想听歌时，根据歌名（可含歌手）搜索并播放音乐。",
        source=ToolSource.LLM_TOOL,
        risk=ToolRisk.NORMAL,
        plugin_name="astrbot_plugin_music",
    )
    rename = ToolDescriptor(
        tool_id="command:取名",
        name="取名",
        description="取名@群友 <消息轮数>",
        source=ToolSource.COMMAND,
        risk=ToolRisk.NORMAL,
        plugin_name="astrbot_plugin_qqadmin",
    )
    catalog = UniversalToolCatalog(types.SimpleNamespace())
    catalog._items = {
        music.tool_id: music,
        rename.tool_id: rename,
    }

    music_hits = catalog.retrieve("小爱放一首远航星的告别")
    rename_hits = catalog.retrieve("小爱给她取个名")

    assert music_hits and music_hits[0].tool_id == music.tool_id
    assert rename_hits and rename_hits[0].tool_id == rename.tool_id


def test_cross_session_send_tools_are_dangerous():
    tool = types.SimpleNamespace(
        name="send_to_group",
        description="发送消息到其他群",
        parameters={"type": "object", "properties": {}},
        active=True,
        handler_module_path="data.plugins.astrbot_plugin_atrelay.main",
        is_background_task=False,
    )
    manager = types.SimpleNamespace(
        get_full_tool_set=lambda: types.SimpleNamespace(tools=[tool]),
        _default_permission=lambda name: "member",
    )
    catalog = UniversalToolCatalog(
        types.SimpleNamespace(get_llm_tool_manager=lambda: manager)
    )

    item = catalog.refresh()[0]

    assert item.risk is ToolRisk.DANGEROUS


def test_persona_renderer_empty_success_does_not_claim_blank():
    class Context:
        async def llm_generate(self, **kwargs):
            prompt = kwargs["prompt"]
            assert "禁止说空白" in prompt
            return types.SimpleNamespace(completion_text="报告发好了呀")

    renderer = AstrBotToolPersonaRenderer(
        Context(),
        lambda group_id: "provider",
        types.SimpleNamespace(system_text=lambda: "persona"),
    )
    text = asyncio.run(
        renderer.final(
            "g1",
            descriptor(),
            ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                "llm:test",
                outputs=(),
            ),
        )
    )
    assert text == "报告发好了呀"


def test_planner_rejects_unknown_tool_and_reports_required_arguments():
    class Context:
        def __init__(self, text):
            self.text = text

        async def llm_generate(self, **kwargs):
            del kwargs
            return types.SimpleNamespace(completion_text=self.text)

    candidate = ToolDescriptor(
        tool_id="llm:analysis",
        name="analysis",
        description="分析群消息",
        source=ToolSource.LLM_TOOL,
        parameters={
            "type": "object",
            "properties": {"days": {"type": "integer"}},
            "required": ["days"],
        },
        risk=ToolRisk.READ_ONLY,
    )
    unknown = AstrBotToolPlanner(
        Context('{"selected":true,"tool_id":"llm:other","arguments":{}}'),
        lambda group_id: "provider",
    )
    missing = AstrBotToolPlanner(
        Context('{"selected":true,"tool_id":"llm:analysis","arguments":{}}'),
        lambda group_id: "provider",
    )

    unknown_plan = asyncio.run(
        unknown.plan(group_id="1", message="分析", candidates=[candidate])
    )
    missing_plan = asyncio.run(
        missing.plan(group_id="1", message="分析", candidates=[candidate])
    )

    assert unknown_plan.selected is False
    assert missing_plan.selected is True
    assert missing_plan.missing_arguments == ("days",)


def test_orchestrator_confirms_empty_success_instead_of_claiming_blank():
    selected = descriptor(native=object())
    delivered = []

    class Catalog:
        def refresh(self):
            return (selected,)

        def retrieve(self, message, limit):
            del message, limit
            return (selected,)

        def get(self, tool_id):
            return selected if tool_id == selected.tool_id else None

    class Planner:
        async def plan(self, **kwargs):
            del kwargs
            return ToolPlan(True, selected.tool_id)

    class Renderer:
        async def progress(self, *args):
            del args
            return "稍等"

        async def final(self, *args):
            del args
            return "报告发好了呀"

    class Executor:
        async def execute(self, *args):
            del args
            return ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                selected.tool_id,
                outputs=(),
            )

    class RecordingOrchestrator(GroupmateToolOrchestrator):
        async def _deliver(self, actor, message, text, **kwargs):
            del actor, message, kwargs
            delivered.append(text)

    class Event(FakeEvent):
        def stop_event(self):
            pass

        def should_call_llm(self, value):
            del value

    actor = types.SimpleNamespace(
        router=types.SimpleNamespace(
            classify=lambda message: types.SimpleNamespace(
                kind=TriggerKind.ALIAS_DIRECT
            )
        ),
        window=types.SimpleNamespace(
            snapshot=lambda: types.SimpleNamespace(messages=())
        ),
    )
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="群友",
        text="小爱分析一个群聊",
        timestamp=100,
    )
    orchestrator = RecordingOrchestrator(
        catalog=Catalog(),
        planner=Planner(),
        renderer=Renderer(),
        executor=Executor(),
        policy=types.SimpleNamespace(
            authorize=lambda descriptor, event: types.SimpleNamespace(allowed=True)
        ),
    )

    handled = asyncio.run(orchestrator.try_handle(Event(), actor, message))

    assert handled is True
    assert delivered == ["稍等", "报告发好了呀"]


def test_analysis_tools_get_long_timeout():
    from groupmate.tools.catalog import _timeout_for_tool

    assert _timeout_for_tool("群分析", "分析群聊日常活动") == 300.0
    assert _timeout_for_tool("禁言", "禁言某人") == 90.0


def test_executor_reports_timeout_when_handler_swallows_cancellation():
    async def handler(event):
        del event
        try:
            await asyncio.sleep(10)
            return "done"
        except asyncio.CancelledError:
            return None

    selected = ToolDescriptor(
        tool_id="llm:test",
        name="test",
        description="测试工具",
        source=ToolSource.LLM_TOOL,
        parameters={"type": "object", "properties": {}},
        risk=ToolRisk.READ_ONLY,
        timeout_seconds=0.05,
        native=types.SimpleNamespace(handler=handler),
    )
    result = asyncio.run(
        HostToolExecutor(types.SimpleNamespace()).execute(
            selected,
            {},
            FakeEvent(),
        )
    )
    assert result.status is ToolExecutionStatus.TIMEOUT
    assert result.error_code == "tool_timeout"


def test_orchestrator_injects_mentioned_user_into_missing_user_id():
    selected = ToolDescriptor(
        tool_id="llm:llm_set_group_ban",
        name="llm_set_group_ban",
        description="在群聊中禁言某用户",
        source=ToolSource.LLM_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "duration": {"type": "number"},
            },
            "required": ["user_id", "duration"],
        },
        risk=ToolRisk.DANGEROUS,
        native=object(),
    )
    executed = []

    class Catalog:
        def refresh(self):
            return (selected,)

        def retrieve(self, message, limit):
            del message, limit
            return (selected,)

        def get(self, tool_id):
            return selected if tool_id == selected.tool_id else None

    class Planner:
        async def plan(self, **kwargs):
            assert "3229586160" in kwargs["message"]
            return ToolPlan(
                True,
                selected.tool_id,
                arguments={"duration": 600},
                missing_arguments=("user_id",),
            )

    class Renderer:
        async def progress(self, *args):
            del args
            return "稍等"

        async def final(self, *args):
            del args
            return "弄好了"

    class Executor:
        async def execute(self, descriptor, arguments, event):
            del descriptor, event
            executed.append(arguments)
            return ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                selected.tool_id,
                outputs=("ok",),
            )

    class RecordingOrchestrator(GroupmateToolOrchestrator):
        async def _deliver(self, actor, message, text, **kwargs):
            del actor, message, text, kwargs

    class Event(FakeEvent):
        def stop_event(self):
            pass

        def should_call_llm(self, value):
            del value

    actor = types.SimpleNamespace(
        router=types.SimpleNamespace(
            classify=lambda message: types.SimpleNamespace(
                kind=TriggerKind.ALIAS_DIRECT
            )
        ),
        window=types.SimpleNamespace(
            snapshot=lambda: types.SimpleNamespace(messages=())
        ),
    )
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="群友",
        text="小爱把她 禁言十分钟",
        timestamp=100,
        mentioned_user_ids=("3229586160",),
    )
    orchestrator = RecordingOrchestrator(
        catalog=Catalog(),
        planner=Planner(),
        renderer=Renderer(),
        executor=Executor(),
        policy=types.SimpleNamespace(
            authorize=lambda descriptor, event: types.SimpleNamespace(allowed=True)
        ),
    )

    handled = asyncio.run(orchestrator.try_handle(Event(), actor, message))

    assert handled is True
    assert executed == [{"duration": 600, "user_id": "3229586160"}]


def test_orchestrator_sends_progress_before_execution_and_claims_event():
    calls = []
    selected = descriptor(native=object())

    class Catalog:
        def refresh(self):
            calls.append("refresh")

        def retrieve(self, message, limit):
            del message, limit
            return (selected,)

        def get(self, tool_id):
            return selected if tool_id == selected.tool_id else None

    class Planner:
        async def plan(self, **kwargs):
            del kwargs
            return ToolPlan(True, selected.tool_id)

    class Renderer:
        async def progress(self, *args):
            del args
            calls.append("render_progress")
            return "等我一下"

        async def final(self, *args):
            del args
            calls.append("render_final")
            return "完成了"

    class Executor:
        async def execute(self, *args):
            del args
            calls.append("execute")
            return ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                selected.tool_id,
                outputs=("结果",),
            )

    class Policy:
        def authorize(self, tool, event):
            del tool, event
            return types.SimpleNamespace(allowed=True)

    class RecordingOrchestrator(GroupmateToolOrchestrator):
        async def _deliver(self, actor, message, text, **kwargs):
            del actor, message, kwargs
            calls.append("deliver:" + text)

    class Event(FakeEvent):
        def __init__(self):
            super().__init__()
            self.stopped = False
            self.call_llm = False

        def stop_event(self):
            self.stopped = True

        def should_call_llm(self, value):
            self.call_llm = value

    actor = types.SimpleNamespace(
        router=types.SimpleNamespace(
            classify=lambda message: types.SimpleNamespace(
                kind=TriggerKind.ALIAS_DIRECT
            )
        ),
        window=types.SimpleNamespace(
            snapshot=lambda: types.SimpleNamespace(messages=())
        ),
    )
    message = ChatMessage(
        message_id="m1",
        group_id="g1",
        sender_id="u1",
        sender_name="群友",
        text="小爱分析群消息",
        timestamp=100,
    )
    event = Event()
    orchestrator = RecordingOrchestrator(
        catalog=Catalog(),
        planner=Planner(),
        renderer=Renderer(),
        executor=Executor(),
        policy=Policy(),
    )

    handled = asyncio.run(orchestrator.try_handle(event, actor, message))

    assert handled is True
    assert event.stopped is True
    assert event.call_llm is True
    assert calls.index("deliver:等我一下") < calls.index("execute")
    assert calls[-1] == "deliver:完成了"


def test_orchestrator_resumes_pending_argument_clarification():
    selected = ToolDescriptor(
        tool_id="llm:analysis",
        name="analysis",
        description="分析群聊",
        source=ToolSource.LLM_TOOL,
        parameters={
            "type": "object",
            "properties": {"days": {"type": "integer"}},
            "required": ["days"],
        },
        risk=ToolRisk.READ_ONLY,
        native=object(),
    )
    plans = [
        ToolPlan(True, selected.tool_id, missing_arguments=("days",)),
        ToolPlan(True, selected.tool_id, arguments={"days": 3}),
    ]
    executed = []

    class Catalog:
        def refresh(self):
            return (selected,)

        def retrieve(self, message, limit):
            del message, limit
            return (selected,)

        def get(self, tool_id):
            return selected if tool_id == selected.tool_id else None

    class Planner:
        async def plan(self, **kwargs):
            del kwargs
            return plans.pop(0)

    class Renderer:
        async def clarification(self, *args):
            del args
            return "要看几天？"

        async def progress(self, *args):
            del args
            return "我去看看"

        async def final(self, *args):
            del args
            return "分析好了"

    class Executor:
        async def execute(self, descriptor, arguments, event):
            del descriptor, event
            executed.append(arguments)
            return ToolExecutionResult(
                ToolExecutionStatus.SUCCESS,
                selected.tool_id,
                outputs=("结果",),
            )

    class RecordingOrchestrator(GroupmateToolOrchestrator):
        async def _deliver(self, actor, message, text, **kwargs):
            del actor, message, text, kwargs

    class Event(FakeEvent):
        def stop_event(self):
            pass

        def should_call_llm(self, value):
            del value

    actor = types.SimpleNamespace(
        router=types.SimpleNamespace(
            classify=lambda message: types.SimpleNamespace(
                kind=(
                    TriggerKind.ALIAS_DIRECT
                    if "分析" in message.text
                    else TriggerKind.CANDIDATE
                )
            )
        ),
        window=types.SimpleNamespace(
            snapshot=lambda: types.SimpleNamespace(messages=())
        ),
        behavior=types.SimpleNamespace(
            conversation=types.SimpleNamespace(continuation_seconds=60)
        ),
        set_continuation=lambda sender_id, expires_at: None,
    )
    orchestrator = RecordingOrchestrator(
        catalog=Catalog(),
        planner=Planner(),
        renderer=Renderer(),
        executor=Executor(),
        policy=types.SimpleNamespace(
            authorize=lambda descriptor, event: types.SimpleNamespace(allowed=True)
        ),
    )
    first = ChatMessage(
        "m1", "g1", "u1", "群友", "小爱分析群消息", 100
    )
    second = ChatMessage("m2", "g1", "u1", "群友", "3天", 110)

    first_handled = asyncio.run(orchestrator.try_handle(Event(), actor, first))
    second_handled = asyncio.run(orchestrator.try_handle(Event(), actor, second))

    assert first_handled is True
    assert second_handled is True
    assert executed == [{"days": 3}]
