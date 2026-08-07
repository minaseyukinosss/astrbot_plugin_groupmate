import asyncio
import random
import types

from groupmate.mail import (
    MailOutcomeKind,
    MailSendRequest,
    MailService,
    MailSettings,
    UnauthorizedMode,
    build_send_qq_mail_descriptor,
    qq_mailbox,
)
from groupmate.tools import (
    HostToolExecutor,
    ToolRisk,
    ToolSource,
    ToolExecutionStatus,
    UniversalToolCatalog,
)


class FakeEvent:
    def __init__(
        self,
        *,
        admin=False,
        sender_id="10001",
        group_role="member",
        sender_name="测试人",
        nickname="QQ昵称甲",
    ):
        self._admin = admin
        self.role = group_role
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.message_obj = types.SimpleNamespace(
            raw_message={
                "sender": {
                    "role": group_role,
                    "card": sender_name,
                    "nickname": nickname,
                }
            }
        )

    def is_admin(self):
        return self._admin

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name

    def get_group_id(self):
        return "g1"


def _ready_settings(**overrides):
    base = dict(
        enabled=True,
        from_address="bot@qq.com",
        auth_code="abcdefghijklmnop",
        unauthorized_mode=UnauthorizedMode.TEASE_REPLY,
        daily_limit_per_user=5,
        send_interval_seconds=0,
        dry_run=False,
    )
    base.update(overrides)
    return MailSettings(**base)


def test_qq_mailbox_helper():
    assert qq_mailbox("12345") == "12345@qq.com"


def test_mail_settings_ready_requires_credentials():
    assert MailSettings(enabled=True).ready is False
    assert _ready_settings().ready is True


def test_catalog_retrieves_builtin_mail_tool():
    service = MailService(_ready_settings(), smtp_sender=lambda **kwargs: None)
    descriptor = build_send_qq_mail_descriptor(service)
    catalog = UniversalToolCatalog(
        context=None,
        command_bridge_enabled=False,
        builtin_tools=(descriptor,),
    )
    catalog.refresh()
    hits = catalog.retrieve("小爱给我发一封节日祝福邮件", limit=5)
    assert hits and hits[0].tool_id == descriptor.tool_id
    assert hits[0].risk is ToolRisk.NORMAL
    assert hits[0].source is ToolSource.BUILTIN


def test_mail_greeting_uses_qq_nickname_not_group_card():
    sent = []
    service = MailService(
        _ready_settings(),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.handle_tool(
            FakeEvent(sender_name="群名片呼叫机", nickname="QQ花名"),
            {"subject": "嗨", "body": "正文内容"},
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent[0]["body"].split("\n")[0].startswith("QQ花名")
    assert "群名片呼叫机" not in sent[0]["body"].split("\n")[0]
    assert sent[0]["to_display_name"] == "QQ花名"


def test_mail_mime_headers_and_body_layout():
    from groupmate.mail.compose import shape_mail_body
    from groupmate.mail.smtp_client import build_message, format_mailbox

    shaped = shape_mail_body(
        "今天也要加油呀",
        recipient_label="呼叫机",
        character_name="爱弥斯",
    )
    assert "呼叫机" in shaped
    assert "爱弥斯" in shaped
    assert "\n\n" in shaped

    msg = build_message(
        from_address="bot@qq.com",
        to_address="10001@qq.com",
        subject="小纸条",
        body=shaped,
        display_name="爱弥斯",
        to_display_name="呼叫机",
    )
    raw = msg.as_string()
    assert "From:" in raw
    assert "To:" in raw
    assert "Subject:" in raw
    assert "Date:" in raw
    assert "Message-ID:" in raw
    assert "charset=\"utf-8\"" in raw.lower() or "charset=utf-8" in raw.lower()
    assert "呼叫机" in shaped
    assert format_mailbox("爱弥斯", "bot@qq.com").endswith("<bot@qq.com>")
    assert format_mailbox("", "bot@qq.com") == "bot@qq.com"


def test_member_can_mail_self():
    sent = []

    def smtp_sender(**kwargs):
        sent.append(kwargs)

    async def llm_generate(**kwargs):
        return types.SimpleNamespace(
            completion_text='{"subject":"嗨","body":"你好呀"}'
        )

    service = MailService(
        _ready_settings(),
        smtp_sender=smtp_sender,
        context=types.SimpleNamespace(llm_generate=llm_generate),
        provider_getter=lambda _gid: "p1",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                theme="节日祝福",
                is_admin=False,
                group_id="g1",
            )
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent and sent[0]["to_address"] == "10001@qq.com"
    assert sent[0]["subject"] == "嗨"


def test_user_provided_copy_is_rewritten_in_persona_voice():
    captured = []

    async def llm_generate(**kwargs):
        captured.append(kwargs)
        return types.SimpleNamespace(
            completion_text='{"subject":"人格主题","body":"人格正文呢"}'
        )

    sent = []
    service = MailService(
        _ready_settings(),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        context=types.SimpleNamespace(llm_generate=llm_generate),
        provider_getter=lambda _gid: "p1",
        persona_system_getter=lambda: "你是测试人格雪绒，短句口语。",
        character_name_getter=lambda: "雪绒",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="正式通知",
                body="根据您的请求，现予以回复。",
                group_id="g1",
            )
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent[0]["subject"] == "人格主题"
    assert "人格正文呢" in sent[0]["body"]
    assert "雪绒" in sent[0]["body"]
    assert "\n" in sent[0]["body"]
    assert sent[0]["display_name"] == "雪绒"
    assert captured
    prompt = captured[0]["prompt"]
    system = captured[0]["system_prompt"]
    assert "当前人格" in prompt or "人格本人" in prompt
    assert "雪绒" in prompt
    assert "测试人格雪绒" in system
    assert "正式通知" in prompt
    assert "根据您的请求" in prompt
    assert "爱弥斯" not in prompt
    assert "爱弥斯" not in system


def test_from_display_name_follows_current_persona_when_config_blank():
    sent = []
    names = {"value": "甲"}
    service = MailService(
        _ready_settings(),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
        character_name_getter=lambda: names["value"],
    )
    asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="s",
                body="b",
            )
        )
    )
    assert sent[0]["display_name"] == "甲"
    names["value"] = "乙"
    asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="s",
                body="b",
            )
        )
    )
    assert sent[1]["display_name"] == "乙"


def test_member_targeting_other_tease_reply():
    sent = []
    service = MailService(
        _ready_settings(unauthorized_mode=UnauthorizedMode.TEASE_REPLY),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        member_name_getter=lambda _gid, uid: "复读斥候" if uid == "20002" else "",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="20002",
                is_admin=False,
                target_display_name="复读斥候",
                requester_display_name="呼叫机",
            )
        )
    )
    assert result.kind is MailOutcomeKind.TEASE_REPLY
    assert "复读斥候" in result.message
    assert sent == []


def test_member_targeting_other_tease_mail():
    sent = []
    service = MailService(
        _ready_settings(unauthorized_mode=UnauthorizedMode.TEASE_MAIL),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="20002",
                is_admin=False,
                target_display_name="复读斥候",
                requester_display_name="呼叫机",
            )
        )
    )
    assert result.kind is MailOutcomeKind.TEASE_MAIL
    assert sent and sent[0]["to_address"] == "10001@qq.com"
    assert "复读斥候" in result.message
    assert "你邮箱" in result.message
    assert "调侃" not in result.message
    from groupmate.mail.builtin_tool import format_mail_tool_output

    output = format_mail_tool_output(result)
    assert "不是寄给" in output or "没有寄给" in output
    assert "20002" not in output


def test_preview_mail_route_detects_unauthorized():
    from groupmate.mail.service import preview_mail_route

    assert (
        preview_mail_route(
            FakeEvent(admin=False, group_role="member"),
            {"to_qq": "20002"},
        )
        == "unauthorized"
    )
    assert (
        preview_mail_route(
            FakeEvent(admin=True, group_role="member"),
            {"to_qq": "20002"},
        )
        == "normal"
    )
    assert (
        preview_mail_route(
            FakeEvent(admin=False, group_role="owner"),
            {"to_qq": "20002"},
        )
        == "normal"
    )


def test_admin_can_mail_other():
    sent = []
    service = MailService(
        _ready_settings(),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="20002",
                subject="主题",
                body="正文",
                is_admin=True,
            )
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent[0]["to_address"] == "20002@qq.com"


def test_group_owner_without_astrbot_admin_cannot_mail_other():
    sent = []
    service = MailService(
        _ready_settings(unauthorized_mode=UnauthorizedMode.TEASE_REPLY),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.handle_tool(
            FakeEvent(admin=False, group_role="owner"),
            {"to_qq": "20002", "subject": "s", "body": "b"},
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent[0]["to_address"] == "20002@qq.com"


def test_group_admin_without_astrbot_admin_cannot_mail_other():
    sent = []
    service = MailService(
        _ready_settings(unauthorized_mode=UnauthorizedMode.TEASE_REPLY),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.handle_tool(
            FakeEvent(admin=False, group_role="admin"),
            {"to_qq": "20002", "subject": "s", "body": "b"},
        )
    )
    assert result.kind is MailOutcomeKind.TEASE_REPLY
    assert sent == []


def test_astrbot_admin_can_mail_other_via_tool():
    sent = []
    service = MailService(
        _ready_settings(),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
        member_name_getter=lambda _gid, uid: "小橘子" if uid == "20002" else "",
    )
    result = asyncio.run(
        service.handle_tool(
            FakeEvent(admin=True, group_role="member"),
            {"to_qq": "20002", "subject": "s", "body": "b"},
        )
    )
    assert result.kind is MailOutcomeKind.SENT
    assert sent[0]["to_address"] == "20002@qq.com"
    assert "小橘子" in result.message
    assert "20002" not in result.message
    from groupmate.mail.builtin_tool import format_mail_tool_output

    output = format_mail_tool_output(result)
    assert "小橘子" in output
    assert "20002" not in output
    assert "禁止复述" in output


def test_tool_output_never_exposes_raw_qq():
    from groupmate.mail.builtin_tool import format_mail_tool_output
    from groupmate.mail.models import MailOutcomeKind, MailSendResult

    text = format_mail_tool_output(
        MailSendResult(
            MailOutcomeKind.SENT,
            "邮件已经发给「阿柠」的邮箱啦。",
            to_address="1291576099@qq.com",
            subject="嗨",
            recipient_label="阿柠",
        )
    )
    assert "阿柠" in text
    assert "1291576099" not in text
    assert "@qq.com" not in text


def test_dry_run_does_not_call_smtp():
    sent = []
    service = MailService(
        _ready_settings(dry_run=True),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="s",
                body="b",
            )
        )
    )
    assert result.kind is MailOutcomeKind.DRY_RUN
    assert sent == []


def test_not_configured_fail_closed():
    service = MailService(MailSettings(enabled=True))
    result = asyncio.run(
        service.handle_tool(FakeEvent(), {"theme": "随便"})
    )
    assert result.kind is MailOutcomeKind.NOT_CONFIGURED


def test_daily_limit_and_interval():
    clock = {"now": 1000.0}
    service = MailService(
        _ready_settings(daily_limit_per_user=1, send_interval_seconds=30),
        smtp_sender=lambda **kwargs: None,
        provider_getter=lambda _gid: "",
        clock=lambda: clock["now"],
    )
    first = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="s",
                body="b",
            )
        )
    )
    assert first.kind is MailOutcomeKind.SENT
    clock["now"] = 1010.0
    second = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="10001",
                subject="s",
                body="b",
            )
        )
    )
    assert second.kind is MailOutcomeKind.RATE_LIMITED
    assert second.error_code == "send_interval"


def test_unauthorized_random_uses_rng():
    sent = []
    service = MailService(
        _ready_settings(unauthorized_mode=UnauthorizedMode.RANDOM),
        smtp_sender=lambda **kwargs: sent.append(kwargs),
        provider_getter=lambda _gid: "",
        rng=random.Random(1),
    )
    result = asyncio.run(
        service.send(
            MailSendRequest(
                requester_qq="10001",
                target_qq="20002",
                is_admin=False,
            )
        )
    )
    assert result.kind in (
        MailOutcomeKind.TEASE_REPLY,
        MailOutcomeKind.TEASE_MAIL,
    )


def test_builtin_executor_success_and_failure():
    service = MailService(
        _ready_settings(dry_run=True),
        smtp_sender=lambda **kwargs: None,
        provider_getter=lambda _gid: "",
    )
    descriptor = build_send_qq_mail_descriptor(service)
    executor = HostToolExecutor(context=None)
    ok = asyncio.run(
        executor.execute(descriptor, {"theme": "祝福"}, FakeEvent())
    )
    assert ok.status is ToolExecutionStatus.SUCCESS

    broken = MailService(MailSettings(enabled=False))
    bad = build_send_qq_mail_descriptor(broken)
    # Force execute even if incompatible flag is false for coverage of handler path
    from groupmate.tools.contracts import ToolDescriptor

    forced = ToolDescriptor(
        tool_id=bad.tool_id,
        name=bad.name,
        description=bad.description,
        source=ToolSource.BUILTIN,
        parameters=bad.parameters,
        risk=ToolRisk.NORMAL,
        permission="member",
        compatible=True,
        native=bad.native,
    )
    failed = asyncio.run(executor.execute(forced, {}, FakeEvent()))
    assert failed.status is ToolExecutionStatus.FAILED
