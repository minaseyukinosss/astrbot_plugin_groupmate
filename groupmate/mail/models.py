"""Immutable mail settings and send outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UnauthorizedMode(str, Enum):
    TEASE_REPLY = "tease_reply"
    TEASE_MAIL = "tease_mail"
    RANDOM = "random"


class MailOutcomeKind(str, Enum):
    SENT = "sent"
    DRY_RUN = "dry_run"
    TEASE_REPLY = "tease_reply"
    TEASE_MAIL = "tease_mail"
    DENIED = "denied"
    RATE_LIMITED = "rate_limited"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


@dataclass(frozen=True)
class MailSettings:
    enabled: bool = False
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    use_ssl: bool = True
    from_address: str = ""
    auth_code: str = ""
    from_display_name: str = ""
    unauthorized_mode: UnauthorizedMode = UnauthorizedMode.RANDOM
    daily_limit_per_user: int = 5
    send_interval_seconds: int = 60
    dry_run: bool = False

    @property
    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.from_address.strip()
            and self.auth_code.strip()
            and self.smtp_host.strip()
            and self.smtp_port > 0
        )


@dataclass(frozen=True)
class MailSendRequest:
    requester_qq: str
    target_qq: str
    theme: str = ""
    subject: str = ""
    body: str = ""
    is_admin: bool = False
    group_id: str = ""
    requester_display_name: str = ""
    target_display_name: str = ""
    requester_qq_nickname: str = ""
    target_qq_nickname: str = ""


@dataclass(frozen=True)
class MailSendResult:
    kind: MailOutcomeKind
    message: str
    to_address: str = ""
    subject: str = ""
    error_code: str = ""
    recipient_label: str = ""
    intended_label: str = ""

    @property
    def success(self) -> bool:
        return self.kind in (
            MailOutcomeKind.SENT,
            MailOutcomeKind.DRY_RUN,
            MailOutcomeKind.TEASE_REPLY,
            MailOutcomeKind.TEASE_MAIL,
        )
