import asyncio
import sys
import types

from groupmate.host import AstrBotPlatformPort
from groupmate.models import SendReceiptKind


class FakeMessageChain:
    def __init__(self):
        self.messages = []
        self.chain = []

    def message(self, text):
        self.messages.append(text)
        return self


class FakeContext:
    def __init__(self):
        self.calls = []

    async def send_message(self, umo, chain):
        self.calls.append(("send_message", umo, chain))
        return True


class FakeReply:
    def __init__(self, *, id):
        self.id = str(id)


class FakeStarTools:
    calls = []

    @classmethod
    async def send_message_by_id(cls, message_type, target_id, chain, platform="aiocqhttp"):
        cls.calls.append((message_type, target_id, chain, platform))


def test_platform_port_uses_event_message_chain(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    star_module = types.ModuleType("astrbot.api.star")
    star_module.StarTools = FakeStarTools

    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)

    context = FakeContext()
    port = AstrBotPlatformPort(context, lambda group_id: "aiocqhttp:GroupMessage:" + group_id)

    async def scenario():
        return await port.send_text("912113397", "在呢。", "decision-1")

    result = asyncio.run(scenario())

    assert len(context.calls) == 1
    _, umo, chain = context.calls[0]
    assert umo == "aiocqhttp:GroupMessage:912113397"
    assert chain.messages == ["在呢。"]
    assert FakeStarTools.calls == []
    assert result.kind is SendReceiptKind.CONFIRMED


def test_platform_port_falls_back_to_group_id_send(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    star_module = types.ModuleType("astrbot.api.star")
    star_module.StarTools = FakeStarTools
    FakeStarTools.calls = []

    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)

    class MissingPlatformContext(FakeContext):
        async def send_message(self, umo, chain):
            self.calls.append(("send_message", umo, chain))
            return False

    context = MissingPlatformContext()
    port = AstrBotPlatformPort(context, lambda group_id: "broken:GroupMessage:" + group_id)

    async def scenario():
        return await port.send_text("912113397", "在呢。", "decision-2")

    result = asyncio.run(scenario())

    assert len(context.calls) == 1
    assert FakeStarTools.calls == [
        ("GroupMessage", "912113397", context.calls[0][2], "aiocqhttp")
    ]
    assert result.kind is SendReceiptKind.UNKNOWN


def test_platform_port_quotes_only_first_segment(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    component_module = types.ModuleType("astrbot.api.message_components")
    component_module.Reply = FakeReply
    star_module = types.ModuleType("astrbot.api.star")
    star_module.StarTools = FakeStarTools
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(
        sys.modules, "astrbot.api.message_components", component_module
    )
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)
    context = FakeContext()
    port = AstrBotPlatformPort(
        context, lambda group_id: "aiocqhttp:GroupMessage:" + group_id
    )

    async def scenario():
        return await port.send_segments(
            "912113397", ("第一句", "第二句"), "decision-quote", "778899"
        )

    result = asyncio.run(scenario())

    first_chain = context.calls[0][2]
    second_chain = context.calls[1][2]
    assert [item.id for item in first_chain.chain if isinstance(item, FakeReply)] == [
        "778899"
    ]
    assert not any(isinstance(item, FakeReply) for item in second_chain.chain)
    assert result.kind is SendReceiptKind.CONFIRMED
