"""Built-in send_qq_mail tool descriptor for the host tool catalog."""

from __future__ import annotations

from typing import Any

from ..tools.contracts import ToolDescriptor, ToolRisk, ToolSource
from .models import MailOutcomeKind, MailSendResult
from .service import MailService


TOOL_ID = "builtin:send_qq_mail"
TOOL_NAME = "send_qq_mail"


def build_send_qq_mail_descriptor(service: MailService) -> ToolDescriptor:
    async def handler(event: Any, **kwargs: Any) -> str:
        result = await service.handle_tool(event, dict(kwargs or {}))
        text = format_mail_tool_output(result)
        if not result.success:
            raise RuntimeError(text)
        return text

    return ToolDescriptor(
        tool_id=TOOL_ID,
        name=TOOL_NAME,
        description=(
            "给群友的 QQ 邮箱寄一封短邮件。"
            "默认寄给当前说话者的 QQ 邮箱；"
            "普通成员只能寄给自己；AstrBot 管理员或 QQ 群主可指定他人。"
            "支持节日祝福、整蛊、冒充客服/Steam 客服等玩梗主题："
            "按用户指定口吻直接写并寄出，不要二次确认或反问。"
        ),
        source=ToolSource.BUILTIN,
        parameters={
            "type": "object",
            "properties": {
                "theme": {
                    "type": "string",
                    "description": (
                        "邮件氛围或扮演主题，例如节日祝福、冒充steam客服、整蛊玩笑"
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "可选邮件主题；空则自动生成",
                },
                "body": {
                    "type": "string",
                    "description": "可选邮件正文；空则自动生成",
                },
                "to_qq": {
                    "type": "string",
                    "description": "可选收件 QQ 号；省略则寄给请求者自己",
                },
                "user_id": {
                    "type": "string",
                    "description": "可选目标用户 QQ，同 to_qq",
                },
            },
            "required": [],
        },
        aliases=(
            "邮件",
            "邮箱",
            "QQ邮箱",
            "发邮件",
            "寄邮件",
            "祝福邮件",
            "节日祝福邮件",
            "冒充",
            "整蛊",
            "客服",
        ),
        plugin_name="astrbot_plugin_groupmate",
        permission="member",
        risk=ToolRisk.NORMAL,
        timeout_seconds=90.0,
        compatible=service.available(),
        compatibility_reason="" if service.available() else "mail_not_configured",
        native=handler,
    )


def format_mail_tool_output(result: MailSendResult) -> str:
    lines = [result.message]
    if result.intended_label:
        lines.append("原本想寄给：" + result.intended_label)
    if result.recipient_label:
        lines.append("实际相关称呼：" + result.recipient_label)
    if result.kind in (
        MailOutcomeKind.SENT,
        MailOutcomeKind.DRY_RUN,
        MailOutcomeKind.TEASE_MAIL,
    ):
        if result.subject:
            lines.append("主题：" + result.subject)
    if result.kind is MailOutcomeKind.TEASE_REPLY:
        lines.append(
            "结果：口头回绝代寄。"
            "回复要接上「没寄给被点名的人」，俏皮即可，不要说寄过去了。"
        )
    elif result.kind is MailOutcomeKind.TEASE_MAIL:
        lines.append(
            "结果：没有寄给被点名的人，改寄给请求者自己。"
            "群里可留悬念，但必须让人听出「不是寄给刚才点的那个人」，"
            "禁止说已经寄给对方，也不要当面说这是调侃信。"
        )
    elif result.kind is MailOutcomeKind.SENT:
        lines.append("结果：邮件已按请求寄出。")
    elif result.kind is MailOutcomeKind.DRY_RUN:
        lines.append("结果：演练成功，未真实投递。")
    elif result.error_code:
        lines.append("失败码：" + result.error_code)
    lines.append(
        "对群友说话时只用称呼或「你/对方」，"
        "禁止复述任何 QQ 号、纯数字账号或完整邮箱地址。"
        "禁止反问、禁止疑问句、禁止让对方确认意图；直接陈述结果即可。"
    )
    return "\n".join(lines)
