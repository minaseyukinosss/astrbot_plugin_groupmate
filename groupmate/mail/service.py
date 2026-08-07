"""Authorize, compose, and send QQ mail for Groupmate."""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .compose import compose_mail
from .models import (
    MailOutcomeKind,
    MailSendRequest,
    MailSendResult,
    MailSettings,
    UnauthorizedMode,
)
from .smtp_client import qq_mailbox, send_smtp


def can_mail_others(event: Any) -> bool:
    """AstrBot admin or QQ group owner; plain group admin is not enough."""

    checker = getattr(event, "is_admin", None)
    if callable(checker):
        try:
            if checker():
                return True
        except Exception:
            pass
    return _sender_role(event) == "owner"


def _sender_role(event: Any) -> str:
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    sender = getattr(raw, "sender", None)
    if isinstance(raw, dict):
        sender = raw.get("sender")
    if isinstance(sender, dict):
        role = str(sender.get("role") or "").lower()
    else:
        role = str(getattr(sender, "role", "") or "").lower()
    if role in ("admin", "owner", "member"):
        return role
    return str(getattr(event, "role", "") or "").lower()


def preview_mail_route(event: Any, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Return ``unauthorized`` when a non-privileged user targets someone else."""

    args = dict(arguments or {})
    requester_qq = _digits(
        getattr(event, "get_sender_id", lambda: "")()
        if callable(getattr(event, "get_sender_id", None))
        else getattr(event, "sender_id", "")
    )
    target_qq = _digits(
        args.get("to_qq")
        or args.get("user_id")
        or args.get("target_id")
        or args.get("target_user_id")
        or requester_qq
    )
    if not target_qq:
        target_qq = requester_qq
    if requester_qq and target_qq and target_qq != requester_qq and not can_mail_others(event):
        return "unauthorized"
    return "normal"


class MailService:
    def __init__(
        self,
        settings: MailSettings,
        *,
        context: Any = None,
        provider_getter: Optional[Callable[[str], str]] = None,
        persona_system_getter: Optional[Callable[[], str]] = None,
        character_name_getter: Optional[Callable[[], str]] = None,
        member_name_getter: Optional[Callable[[str, str], str]] = None,
        qq_nickname_getter: Optional[Callable[[str, str], Any]] = None,
        smtp_sender: Optional[Callable[..., None]] = None,
        clock: Optional[Callable[[], float]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.settings = settings
        self.context = context
        self.provider_getter = provider_getter or (lambda _group_id: "")
        self.persona_system_getter = persona_system_getter or (lambda: "")
        self.character_name_getter = character_name_getter or (lambda: "")
        self.member_name_getter = member_name_getter
        self.qq_nickname_getter = qq_nickname_getter
        self.smtp_sender = smtp_sender or send_smtp
        self.clock = clock or time.time
        self.rng = rng or random.Random()
        self._daily_counts: Dict[Tuple[str, str], int] = {}
        self._last_send_at: Dict[str, float] = {}

    def available(self) -> bool:
        return self.settings.ready

    def current_character_name(self) -> str:
        try:
            return str(self.character_name_getter() or "").strip()
        except Exception:
            return ""

    def current_from_display_name(self) -> str:
        configured = str(self.settings.from_display_name or "").strip()
        if configured:
            return configured
        return self.current_character_name()

    async def handle_tool(
        self,
        event: Any,
        arguments: Dict[str, Any],
    ) -> MailSendResult:
        if not self.settings.ready:
            return MailSendResult(
                MailOutcomeKind.NOT_CONFIGURED,
                "邮件功能还没配好，这会儿发不了。",
                error_code="mail_not_configured",
            )

        requester_qq = _digits(
            getattr(event, "get_sender_id", lambda: "")()
            if callable(getattr(event, "get_sender_id", None))
            else getattr(event, "sender_id", "")
        )
        if not requester_qq:
            return MailSendResult(
                MailOutcomeKind.FAILED,
                "认不出你是谁，邮件没发出去。",
                error_code="missing_requester",
            )

        target_qq = _digits(
            arguments.get("to_qq")
            or arguments.get("user_id")
            or arguments.get("target_id")
            or arguments.get("target_user_id")
            or requester_qq
        )
        if not target_qq:
            target_qq = requester_qq

        group_id = str(
            getattr(event, "get_group_id", lambda: "")()
            if callable(getattr(event, "get_group_id", None))
            else getattr(event, "group_id", "")
            or ""
        )
        mention_names = _mention_display_names(event)
        requester_group_name = public_member_label(
            _event_sender_group_name(event)
            or _lookup_member_name(self.member_name_getter, group_id, requester_qq),
            requester_qq,
            fallback="你",
        )
        target_group_name = public_member_label(
            str(arguments.get("to_name") or arguments.get("target_name") or "").strip()
            or mention_names.get(target_qq, "")
            or (
                _event_sender_group_name(event)
                if target_qq == requester_qq
                else ""
            )
            or _lookup_member_name(self.member_name_getter, group_id, target_qq),
            target_qq,
            fallback="对方",
        )
        requester_qq_nickname = await self._resolve_qq_nickname(
            group_id,
            requester_qq,
            event_nickname=_event_sender_qq_nickname(event),
        )
        target_qq_nickname = requester_qq_nickname
        if target_qq != requester_qq:
            target_qq_nickname = await self._resolve_qq_nickname(
                group_id,
                target_qq,
                event_nickname=str(
                    arguments.get("to_qq_nickname")
                    or arguments.get("nickname")
                    or ""
                ).strip(),
            )
        request = MailSendRequest(
            requester_qq=requester_qq,
            target_qq=target_qq,
            theme=str(arguments.get("theme") or "").strip(),
            subject=str(arguments.get("subject") or "").strip(),
            body=str(arguments.get("body") or "").strip(),
            is_admin=can_mail_others(event),
            group_id=group_id,
            requester_display_name=requester_group_name,
            target_display_name=target_group_name,
            requester_qq_nickname=requester_qq_nickname,
            target_qq_nickname=target_qq_nickname,
        )
        return await self.send(request)

    async def send(self, request: MailSendRequest) -> MailSendResult:
        if not self.settings.ready:
            return MailSendResult(
                MailOutcomeKind.NOT_CONFIGURED,
                "邮件功能还没配好，这会儿发不了。",
                error_code="mail_not_configured",
            )

        try:
            _ = qq_mailbox(request.requester_qq)
            _ = qq_mailbox(request.target_qq)
        except ValueError:
            return MailSendResult(
                MailOutcomeKind.FAILED,
                "QQ 号不对，邮件没发出去。",
                error_code="invalid_qq",
            )

        if request.target_qq != request.requester_qq and not request.is_admin:
            return await self._handle_unauthorized(request)

        limited = self._check_rate_limit(request.requester_qq)
        if limited is not None:
            return limited

        to_self = request.target_qq == request.requester_qq
        # Mail greeting must use QQ nickname, not group card / “你”.
        recipient_label = mail_qq_greeting_name(request, for_mailbox_owner=to_self)
        chat_label = (
            public_member_label(
                request.requester_display_name,
                request.requester_qq,
                fallback="你",
            )
            if to_self
            else public_member_label(
                request.target_display_name,
                request.target_qq,
                fallback="对方",
            )
        )
        subject, body = await self._compose(
            request,
            tease=False,
            recipient_label=recipient_label,
        )
        to_address = qq_mailbox(request.target_qq)
        if to_self:
            success_message = "邮件已经发到你的 QQ 邮箱啦。"
        else:
            success_message = "邮件已经发给「{}」的邮箱啦。".format(chat_label)
        return await self._deliver(
            requester_qq=request.requester_qq,
            to_address=to_address,
            subject=subject,
            body=body,
            success_kind=MailOutcomeKind.SENT,
            success_message=success_message,
            recipient_label=chat_label,
            to_display_name=recipient_label,
        )

    async def _handle_unauthorized(self, request: MailSendRequest) -> MailSendResult:
        mode = self.settings.unauthorized_mode
        if mode is UnauthorizedMode.RANDOM:
            mode = self.rng.choice(
                (UnauthorizedMode.TEASE_REPLY, UnauthorizedMode.TEASE_MAIL)
            )
        if mode is UnauthorizedMode.TEASE_REPLY:
            intended = public_member_label(
                request.target_display_name,
                request.target_qq,
                fallback="对方",
            )
            return MailSendResult(
                MailOutcomeKind.TEASE_REPLY,
                "想给「{}」发？这事你可做不了主哦。".format(intended),
                intended_label=intended,
                recipient_label=public_member_label(
                    request.requester_display_name,
                    request.requester_qq,
                    fallback="你",
                ),
            )

        limited = self._check_rate_limit(request.requester_qq)
        if limited is not None:
            return limited

        intended = public_member_label(
            request.target_display_name,
            request.target_qq,
            fallback="对方",
        )
        subject, body = await self._compose(
            request,
            tease=True,
            recipient_label=mail_qq_greeting_name(request, for_mailbox_owner=True),
        )
        to_address = qq_mailbox(request.requester_qq)
        requester_label = public_member_label(
            request.requester_display_name,
            request.requester_qq,
            fallback="你",
        )
        return await self._deliver(
            requester_qq=request.requester_qq,
            to_address=to_address,
            subject=subject,
            body=body,
            success_kind=MailOutcomeKind.TEASE_MAIL,
            success_message=(
                "没寄给「{}」啦 先塞了点东西到你邮箱 "
                "自己拆开看吧"
            ).format(intended),
            recipient_label=requester_label,
            intended_label=intended,
            to_display_name=mail_qq_greeting_name(request, for_mailbox_owner=True),
        )

    async def _compose(
        self,
        request: MailSendRequest,
        *,
        tease: bool,
        recipient_label: str,
    ) -> Tuple[str, str]:
        provider_id = self.provider_getter(request.group_id)
        persona_system = ""
        try:
            persona_system = str(self.persona_system_getter() or "")
        except Exception:
            persona_system = ""
        return await compose_mail(
            context=self.context,
            provider_id=provider_id,
            persona_system=persona_system,
            theme=request.theme,
            subject=request.subject,
            body=request.body,
            recipient_label=recipient_label,
            tease=tease,
            character_name=self.current_character_name(),
        )

    async def _deliver(
        self,
        *,
        requester_qq: str,
        to_address: str,
        subject: str,
        body: str,
        success_kind: MailOutcomeKind,
        success_message: str,
        recipient_label: str = "",
        intended_label: str = "",
        to_display_name: str = "",
    ) -> MailSendResult:
        if self.settings.dry_run:
            self._record_send(requester_qq)
            return MailSendResult(
                MailOutcomeKind.DRY_RUN,
                success_message + "（演练模式，未真实投递）",
                to_address=to_address,
                subject=subject,
                recipient_label=recipient_label,
                intended_label=intended_label,
            )
        try:
            self.smtp_sender(
                host=self.settings.smtp_host,
                port=self.settings.smtp_port,
                use_ssl=self.settings.use_ssl,
                username=self.settings.from_address,
                password=self.settings.auth_code,
                from_address=self.settings.from_address,
                to_address=to_address,
                subject=subject,
                body=body,
                display_name=self.current_from_display_name(),
                to_display_name=to_display_name,
            )
        except Exception as exc:
            return MailSendResult(
                MailOutcomeKind.FAILED,
                "邮件没发出去，晚点再试吧。",
                to_address=to_address,
                subject=subject,
                error_code=exc.__class__.__name__,
                recipient_label=recipient_label,
                intended_label=intended_label,
            )
        self._record_send(requester_qq)
        return MailSendResult(
            success_kind,
            success_message,
            to_address=to_address,
            subject=subject,
            recipient_label=recipient_label,
            intended_label=intended_label,
        )

    async def _resolve_qq_nickname(
        self,
        group_id: str,
        user_id: str,
        *,
        event_nickname: str = "",
    ) -> str:
        direct = public_member_label(event_nickname, user_id, fallback="")
        if direct:
            return direct
        getter = self.qq_nickname_getter
        if not callable(getter):
            return ""
        try:
            result = getter(str(group_id), str(user_id))
            if hasattr(result, "__await__"):
                result = await result
            return public_member_label(result, user_id, fallback="")
        except Exception:
            return ""

    def _check_rate_limit(self, requester_qq: str) -> Optional[MailSendResult]:
        now = float(self.clock())
        last = self._last_send_at.get(requester_qq)
        interval = max(0, int(self.settings.send_interval_seconds))
        if last is not None and interval > 0 and now - last < interval:
            return MailSendResult(
                MailOutcomeKind.RATE_LIMITED,
                "发太勤了，稍等一会儿再让我寄吧。",
                error_code="send_interval",
            )
        day_key = (requester_qq, _utc_day(now))
        count = self._daily_counts.get(day_key, 0)
        limit = max(0, int(self.settings.daily_limit_per_user))
        if limit > 0 and count >= limit:
            return MailSendResult(
                MailOutcomeKind.RATE_LIMITED,
                "今天寄得够多啦，明天再说吧。",
                error_code="daily_limit",
            )
        return None

    def _record_send(self, requester_qq: str) -> None:
        now = float(self.clock())
        self._last_send_at[requester_qq] = now
        day_key = (requester_qq, _utc_day(now))
        self._daily_counts[day_key] = self._daily_counts.get(day_key, 0) + 1


def mail_qq_greeting_name(
    request: MailSendRequest,
    *,
    for_mailbox_owner: bool,
) -> str:
    """Prefer QQ nickname for in-mail greeting; never use group card as first choice."""

    if for_mailbox_owner:
        return public_member_label(
            request.requester_qq_nickname or request.requester_display_name,
            request.requester_qq,
            fallback="朋友",
        )
    return public_member_label(
        request.target_qq_nickname or request.target_display_name,
        request.target_qq,
        fallback="朋友",
    )


def public_member_label(name: Any, qq: str, *, fallback: str = "对方") -> str:
    cleaned = str(name or "").strip()
    qq_text = str(qq or "").strip()
    if not cleaned or cleaned == qq_text or cleaned.isdigit():
        return fallback
    if qq_text and qq_text in cleaned:
        return fallback
    return cleaned[:40]


def _lookup_member_name(
    getter: Optional[Callable[[str, str], str]],
    group_id: str,
    user_id: str,
) -> str:
    if not callable(getter):
        return ""
    try:
        return str(getter(str(group_id), str(user_id)) or "").strip()
    except Exception:
        return ""


def _sender_dict(event: Any) -> Dict[str, Any]:
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    sender = getattr(raw, "sender", None)
    if isinstance(raw, dict):
        sender = raw.get("sender")
    if isinstance(sender, dict):
        return sender
    if sender is None:
        return {}
    return {
        "card": getattr(sender, "card", ""),
        "nickname": getattr(sender, "nickname", ""),
    }


def _event_sender_qq_nickname(event: Any) -> str:
    sender = _sender_dict(event)
    return str(sender.get("nickname") or "").strip()


def _event_sender_group_name(event: Any) -> str:
    getter = getattr(event, "get_sender_name", None)
    if callable(getter):
        try:
            name = str(getter() or "").strip()
            if name:
                return name
        except Exception:
            pass
    sender = _sender_dict(event)
    return str(sender.get("card") or sender.get("nickname") or "").strip()


def _mention_display_names(event: Any) -> Dict[str, str]:
    names: Dict[str, str] = {}
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    segments = ()
    if isinstance(raw, dict):
        segments = raw.get("message") or ()
    if not isinstance(segments, (list, tuple)):
        return names
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if str(segment.get("type", "")).lower() != "at":
            continue
        data = segment.get("data") or {}
        qq = _digits(data.get("qq") or data.get("user_id"))
        name = str(data.get("name") or data.get("display_name") or "").strip()
        if qq and name and not name.isdigit():
            names[qq] = name
    return names


def _digits(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return text
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits if digits.isdigit() and digits else ""


def _utc_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
