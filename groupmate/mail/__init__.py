"""Groupmate built-in QQ mail capability."""

from .builtin_tool import TOOL_ID, TOOL_NAME, build_send_qq_mail_descriptor
from .models import (
    MailOutcomeKind,
    MailSendRequest,
    MailSendResult,
    MailSettings,
    UnauthorizedMode,
)
from .service import MailService, preview_mail_route
from .smtp_client import format_mailbox, normalize_mail_body, qq_mailbox, send_smtp

__all__ = [
    "TOOL_ID",
    "TOOL_NAME",
    "MailOutcomeKind",
    "MailSendRequest",
    "MailSendResult",
    "MailService",
    "MailSettings",
    "UnauthorizedMode",
    "build_send_qq_mail_descriptor",
    "format_mailbox",
    "normalize_mail_body",
    "preview_mail_route",
    "qq_mailbox",
    "send_smtp",
]
