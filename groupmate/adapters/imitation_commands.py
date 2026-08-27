"""Deterministic recognition of group-scoped imitation state requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ImitationRequest:
    kind: str
    group_id: str
    requester_id: str
    target_member_id: str | None = None
    target_display_name: str | None = None
    expires_at: int | None = None


class ImitationCommandError(ValueError):
    def __init__(self, code: str, user_text: str) -> None:
        self.code = str(code)
        self.user_text = str(user_text)
        super().__init__(self.code)


class ImitationCommandInterpreter:
    MAX_DURATION_SECONDS = 3 * 24 * 60 * 60
    _START = re.compile(r"(?:开始|从现在开始|接下来)?.{0,6}(?:模仿|学着?|学学).{0,10}(?:说话|讲话|口吻|语气)?")
    _STOP_SELF = re.compile(r"(?:别|不要|停止)(?:再)?(?:学|模仿)我")
    _STOP_ANY = re.compile(r"(?:停止|结束|取消)(?:当前)?模仿")
    _DURATION = re.compile(r"(?:持续|学)([一二两三四五六七八九十\d]+)(分钟|小时|天)")
    _DAY_CLOCK = re.compile(r"到(今晚|明晚|明天)(?:的)?(?:上午|下午|晚上)?\s*([一二两三四五六七八九十\d]{1,3})[点时](半|\d{1,2}分)?")
    _EXACT = re.compile(r"到\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2})[:点时](\d{1,2})?")

    def __init__(self, *, admin_ids, participants, timezone=None) -> None:
        self.admin_ids = frozenset(str(item).strip() for item in admin_ids)
        self.participants = participants
        self.timezone = timezone or ZoneInfo("Asia/Shanghai")

    def interpret(self, event, *, now: int) -> ImitationRequest | None:
        payload = event.payload
        if not payload.get("mentions_bot"):
            return None
        text = " ".join(str(payload.get("text") or "").split())
        group_id = str(event.group_id or "")
        requester = str(event.actor_id or "")
        if self._STOP_SELF.search(text):
            return ImitationRequest("STOP_SELF", group_id, requester, requester)
        if self._STOP_ANY.search(text):
            if requester not in self.admin_ids:
                raise ImitationCommandError(
                    "imitation_admin_required", "只有配置管理员能结束其他人的模仿。"
                )
            return ImitationRequest("STOP_ADMIN", group_id, requester)
        if not self._START.search(text):
            return None
        if requester not in self.admin_ids:
            raise ImitationCommandError(
                "imitation_admin_required", "这件事只有配置里的管理员能让我开始。"
            )
        target = self._target(event, text)
        expiry = self._expiry(text, now=int(now))
        return ImitationRequest(
            "START",
            group_id,
            requester,
            str(target["actor_id"]),
            str(target["display_name"]),
            expiry,
        )

    def _target(self, event, text: str) -> dict[str, str]:
        payload = event.payload
        bot_id = str(payload.get("bot_id") or "")
        mentioned = tuple(
            dict.fromkeys(
                str(item) for item in payload.get("mentions", ())
                if str(item) and str(item) != bot_id
            )
        )
        if len(mentioned) > 1:
            raise ImitationCommandError(
                "imitation_target_ambiguous", "一次只 @一位要模仿的群友。"
            )
        if mentioned:
            member = self.participants.resolve_actor(
                persona_id=event.persona_id,
                group_id=str(event.group_id),
                actor_id=mentioned[0],
            )
            if member is None:
                raise ImitationCommandError(
                    "imitation_target_not_found", "我在当前群里还认不出这位成员。"
                )
            return {"actor_id": mentioned[0], **member}
        matches = self.participants.matching_display_names(
            persona_id=event.persona_id,
            group_id=str(event.group_id),
            text=text,
        )
        if not matches:
            raise ImitationCommandError(
                "imitation_target_required", "直接 @一下要我模仿的人。"
            )
        if len(matches) > 1:
            raise ImitationCommandError(
                "imitation_target_ambiguous", "这个名字对不上唯一的人，直接 @他一次。"
            )
        return matches[0]

    def _expiry(self, text: str, *, now: int) -> int:
        current = datetime.fromtimestamp(now, tz=self.timezone)
        duration = self._DURATION.search(text)
        if duration:
            amount = self._number(duration.group(1))
            seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400}[duration.group(2)]
            return self._validate_expiry(now, now + seconds)
        exact = self._EXACT.search(text)
        if exact:
            year, month, day, hour = (int(exact.group(i)) for i in range(1, 5))
            minute = int(exact.group(5) or 0)
            try:
                expiry = int(datetime(year, month, day, hour, minute, tzinfo=self.timezone).timestamp())
            except ValueError:
                raise ImitationCommandError(
                    "imitation_expiry_invalid", "这个结束时间不太对，换个明确的未来时间。"
                ) from None
            return self._validate_expiry(now, expiry)
        clock = self._DAY_CLOCK.search(text)
        if clock:
            label, hour = clock.group(1), self._number(clock.group(2))
            minute = 30 if clock.group(3) == "半" else int((clock.group(3) or "0分").rstrip("分"))
            day_offset = 1 if label in {"明晚", "明天"} else 0
            if label in {"今晚", "明晚"} and hour < 12:
                hour += 12
            expiry_dt = (current + timedelta(days=day_offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            return self._validate_expiry(now, int(expiry_dt.timestamp()))
        raise ImitationCommandError(
            "imitation_expiry_required", "得告诉我学到什么时候，单次最长三天。"
        )

    def _validate_expiry(self, now: int, expiry: int) -> int:
        if expiry <= now:
            raise ImitationCommandError(
                "imitation_expiry_past", "结束时间得在现在之后。"
            )
        if expiry - now > self.MAX_DURATION_SECONDS:
            raise ImitationCommandError(
                "imitation_duration_too_long", "单次最长三天，时间再缩短一点。"
            )
        return expiry

    @staticmethod
    def _number(value: str) -> int:
        if value.isdigit():
            return int(value)
        values = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if value in values:
            return values[value]
        if value.startswith("十"):
            return 10 + values.get(value[1:], 0)
        if "十" in value:
            left, right = value.split("十", 1)
            return values.get(left, 0) * 10 + values.get(right, 0)
        raise ImitationCommandError("imitation_expiry_invalid", "时间数字没看明白。")


__all__ = (
    "ImitationCommandError",
    "ImitationCommandInterpreter",
    "ImitationRequest",
)
