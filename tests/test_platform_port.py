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
    port = AstrBotPlatformPort(context, lambda group_id: "aiocqhttp:GroupMessage:" + group_id)

    async def scenario():
        return await port.send_outbound(
            "912113397",
            (OutboundSegment(OutboundKind.TEXT, text="在呢。"),),
            "decision-1",
        )

    result = asyncio.run(scenario())

    assert len(context.calls) == 1
    _, umo, chain = context.calls[0]
    assert umo == "aiocqhttp:GroupMessage:912113397"
    assert [item.text for item in chain.chain if isinstance(item, FakePlain)] == [
        "在呢。"
    ]
    assert FakeStarTools.calls == []
    assert result.kind is SendReceiptKind.CONFIRMED


def test_platform_port_falls_back_to_group_id_send(monkeypatch):
    event_module = types.ModuleType("astrbot.api.event")
    event_module.MessageChain = FakeMessageChain
    component_module = types.ModuleType("astrbot.api.message_components")
    component_module.Reply = FakeReply
    component_module.Plain = FakePlain
    component_module.Image = FakeImage
    star_module = types.ModuleType("astrbot.api.star")
    star_module.StarTools = FakeStarTools
    FakeStarTools.calls = []

    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(
        sys.modules, "astrbot.api.message_components", component_module
    )
    monkeypatch.setitem(sys.modules, "astrbot.api.star", star_module)

    class MissingPlatformContext(FakeContext):
        async def send_message(self, umo, chain):
            self.calls.append(("send_message", umo, chain))
            return False

    context = MissingPlatformContext()
    port = AstrBotPlatformPort(context, lambda group_id: "broken:GroupMessage:" + group_id)

    async def scenario():
        return await port.send_outbound(
            "912113397",
            (OutboundSegment(OutboundKind.TEXT, text="在呢。"),),
            "decision-2",
        )

    result = asyncio.run(scenario())

    assert len(context.calls) == 1
    assert FakeStarTools.calls == [
        ("GroupMessage", "912113397", context.calls[0][2], "aiocqhttp")
    ]
    assert result.kind is SendReceiptKind.UNKNOWN


def test_platform_port_quotes_ordered_text_segments_once(monkeypatch):
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

    async def scenario():
        return await port.send_outbound(
            "912113397",
            (
                OutboundSegment(OutboundKind.TEXT, text="第一句"),
                OutboundSegment(OutboundKind.TEXT, text="第二句"),
            ),
            "decision-quote",
            "778899",
        )

    result = asyncio.run(scenario())

    assert len(context.calls) == 1
    chain = context.calls[0][2].chain
    assert [item.id for item in chain if isinstance(item, FakeReply)] == [
        "778899"
    ]
    assert [item.text for item in chain if isinstance(item, FakePlain)] == [
        "第一句",
        "第二句",
    ]
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


class FakePokeClient:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **payload):
        self.calls.append((action, payload))
        if action not in ("group_poke", "send_poke", "friend_poke"):
            raise RuntimeError("unsupported")


class FakePokePlatform:
    def __init__(self, client):
        self._client = client
        self.meta = lambda: types.SimpleNamespace(name="aiocqhttp")

    def get_client(self):
        return self._client


def test_platform_port_sends_poke_then_text(monkeypatch):
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

    client = FakePokeClient()
    context = FakeContext()
    context.get_platform = lambda name: FakePokePlatform(client)
    port = AstrBotPlatformPort(
        context, lambda group_id: "aiocqhttp:GroupMessage:" + group_id
    )

    result = asyncio.run(
        port.send_outbound(
            "912113397",
            (
                OutboundSegment(OutboundKind.POKE, target_user_id="10001"),
                OutboundSegment(OutboundKind.TEXT, text="别戳啦。"),
            ),
            "decision-poke",
        )
    )

    assert result.kind is SendReceiptKind.CONFIRMED
    assert client.calls[0][0] == "group_poke"
    assert client.calls[0][1]["user_id"] == 10001
    assert len(context.calls) == 1


def test_platform_port_keeps_text_when_poke_client_missing(monkeypatch):
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
    port = AstrBotPlatformPort(
        context, lambda group_id: "aiocqhttp:GroupMessage:" + group_id
    )

    result = asyncio.run(
        port.send_outbound(
            "912113397",
            (
                OutboundSegment(OutboundKind.POKE, target_user_id="10001"),
                OutboundSegment(OutboundKind.TEXT, text="别戳啦。"),
            ),
            "decision-poke-fallback",
        )
    )

    assert result.kind is SendReceiptKind.CONFIRMED
    assert len(context.calls) == 1
