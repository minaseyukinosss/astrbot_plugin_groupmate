import asyncio
import sys
import types

from groupmate.host import AstrBotPlatformPort
from groupmate.models import (
    OutboundKind,
    OutboundSegment,
    SendReceiptKind,
)


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


class FakePlain:
    def __init__(self, text):
        self.text = str(text)


class FakeImage:
    def __init__(self, source, value):
        self.source = source
        self.value = str(value)

    @classmethod
    def fromURL(cls, value):
        return cls("url", value)

    @classmethod
    def fromFileSystem(cls, value):
        return cls("file", value)


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


def test_platform_port_sends_ordered_quote_text_and_images_in_one_chain(
    monkeypatch, tmp_path
):
    local_image = tmp_path / "reaction.png"
    local_image.write_bytes(b"image")
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    component_module = types.ModuleType("astrbot.api.message_components")
    component_module.Reply = FakeReply
    component_module.Plain = FakePlain
    component_module.Image = FakeImage
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

    result = asyncio.run(
        port.send_outbound(
            "912113397",
            (
                OutboundSegment(OutboundKind.TEXT, text="给你看"),
                OutboundSegment(
                    OutboundKind.IMAGE,
                    media_id="remote-1",
                    media_ref="https://example.test/result.png",
                ),
                OutboundSegment(
                    OutboundKind.IMAGE,
                    media_id="local-1",
                    media_ref=str(local_image),
                ),
            ),
            "decision-rich",
            quote_message_id="778899",
        )
    )

    assert len(context.calls) == 1
    chain = context.calls[0][2].chain
    assert [type(item).__name__ for item in chain] == [
        "FakeReply",
        "FakePlain",
        "FakeImage",
        "FakeImage",
    ]
    assert chain[0].id == "778899"
    assert chain[1].text == "给你看"
    assert (chain[2].source, chain[2].value) == (
        "url",
        "https://example.test/result.png",
    )
    assert (chain[3].source, chain[3].value) == ("file", str(local_image))
    assert result.kind is SendReceiptKind.CONFIRMED


def test_platform_port_rejects_relative_image_ref_before_send(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    component_module = types.ModuleType("astrbot.api.message_components")
    component_module.Reply = FakeReply
    component_module.Plain = FakePlain
    component_module.Image = FakeImage
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(
        sys.modules, "astrbot.api.message_components", component_module
    )
    context = FakeContext()
    port = AstrBotPlatformPort(context, lambda group_id: "umo:" + group_id)

    result = asyncio.run(
        port.send_outbound(
            "g",
            (
                OutboundSegment(
                    OutboundKind.IMAGE,
                    media_id="bad-ref",
                    media_ref="../relative.png",
                ),
            ),
            "decision-bad",
        )
    )

    assert result.kind is not SendReceiptKind.CONFIRMED
    assert result.error_code == "invalid_media_ref"
    assert context.calls == []


def test_platform_port_rejects_quote_only_outbound(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    component_module = types.ModuleType("astrbot.api.message_components")
    component_module.Reply = FakeReply
    component_module.Plain = FakePlain
    component_module.Image = FakeImage
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(
        sys.modules, "astrbot.api.message_components", component_module
    )
    context = FakeContext()
    port = AstrBotPlatformPort(context, lambda group_id: "umo:" + group_id)

    result = asyncio.run(
        port.send_outbound(
            "g",
            (),
            "decision-empty",
            quote_message_id="778899",
        )
    )

    assert result.error_code == "empty_outbound"
    assert context.calls == []
