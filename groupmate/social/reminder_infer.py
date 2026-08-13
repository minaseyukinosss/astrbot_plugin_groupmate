"""Deterministic timed-reminder recovery for self-commitment ledger writes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

SHORT_TIMER_SECONDS = 15 * 60
MAX_OFFSET_SECONDS = 7 * 24 * 3600
_DEFAULT_TZ = "Asia/Shanghai"

_DELAY_CORE = (
    r"(?:(?P<half>半)\s*(?P<half_unit>个?小时|小时|個?小時|分钟|分鐘|分)"
    r"|(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>个?小时|小时|個?小時|分鐘|分钟|分|秒钟|秒鐘|秒))"
)
_RELATIVE_DELAY = re.compile(_DELAY_CORE)
_REQUEST_HINT = re.compile(
    r"(提醒我|叫我|喊我|叮我|提醒一下|到点提醒|到點提醒|帮我记|幫我記|记一下|記一下)"
)
_BOUND_BEFORE = re.compile(
    _DELAY_CORE
    + r"\s*(?:后|後|之後|之后|倒计时|倒計時)?"
    + r"\s*(?:再)?"
    + r"\s*(?:提醒我|叫我|喊我|叮我|提醒一下|到点提醒|到點提醒|帮我记|幫我記)"
)
_BOUND_AFTER = re.compile(
    r"(?:提醒我|叫我|喊我|叮我|提醒一下|到点提醒|到點提醒|帮我记|幫我記|记一下|記一下)"
    + r".{0,16}?"
    + _DELAY_CORE
    + r"\s*(?:后|後|之後|之后|倒计时|倒計時)?"
)
_ACCEPT_HINT = re.compile(
    r"(倒计时开始|倒計時開始|开始倒计时|開始倒計時|"
    r"到时候提醒|到時候提醒|到点提醒|到點提醒|"
    r"到点我喊|到點我喊|到点喊你|到點喊你|到时候喊你|到時候喊你|"
    r"喊你一声|喊你一聲|"
    r"提醒你|我会提醒|我會提醒|帮你盯|幫你盯|"
    r"记下(?:了|啦|哦)?|記下(?:了|啦|哦)?)"
)
_PREMATURE_DUE_PHRASE = re.compile(
    r"(到时间了|時間到了|时间到了|到点了|到點了|该交了|該交了)"
)
_CANCEL_STRONG = re.compile(
    r"("
    r"取消提醒|取消倒计时|取消倒計時|"
    r"不用(?:再)?提醒(?:我)?了|"
    r"别(?:再)?提醒(?:我)?了|別(?:再)?提醒(?:我)?了|"
    r"不用(?:再)?喊(?:我)?了|别(?:再)?喊(?:我)?了|別(?:再)?喊(?:我)?了|"
    r"不用(?:再)?叫(?:我)?了|别(?:再)?叫(?:我)?了|別(?:再)?叫(?:我)?了|"
    r"(?:别|別|不用)(?:再)?提醒"
    r")"
)
_CANCEL_SOFT = re.compile(r"(算了|不用了|取消吧|算了吧)")
_TIME_NOISE = re.compile(
    r"(?:半\s*(?:个?小时|小时|個?小時|分钟|分鐘|分)|"
    r"\d+(?:\.\d+)?\s*(?:个?小时|小时|個?小時|分鐘|分钟|分|秒钟|秒鐘|秒))"
    r"(?:\s*(?:后|後|之後|之后|倒计时|倒計時))?"
)
_ABSOLUTE_NOISE = re.compile(
    r"(?:今天|今日|明天|明日|后天|後天|今晚|明晚)?"
    r"(?:早上|上午|中午|下午|晚上|傍晚|凌晨)?"
    r"(?:十二|十一|十|[一二三四五六七八九两兩]|\d{1,2})\s*[点點时時:]"
    r"(?:半|\d{1,2}\s*分?)?"
)
_REQUEST_NOISE = re.compile(
    r"(小爱|小愛|爱弥斯|愛彌斯|@\S+|提醒我|叫我|喊我|叮我|"
    r"提醒一下|请|請|帮我|幫我|麻烦|麻煩|记一下|記一下)"
)
_ABSOLUTE_TIME = re.compile(
    r"(?:(?P<day>今天|今日|明天|明日|后天|後天|今晚|明晚))"
    r"(?P<period>早上|上午|中午|下午|晚上|傍晚|凌晨)?"
    r"(?P<hour>十二|十一|十|[一二三四五六七八九两兩]|\d{1,2})\s*[点點时時:]"
    r"(?P<minute>半|\d{1,2}\s*分?)?"
    r"|"
    r"(?P<period2>早上|上午|中午|下午|晚上|傍晚|凌晨)"
    r"(?P<hour2>十二|十一|十|[一二三四五六七八九两兩]|\d{1,2})\s*[点點时時:]"
    r"(?P<minute2>半|\d{1,2}\s*分?)?"
)
_CN_HOUR = {
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


@dataclass(frozen=True)
class TimedReminderRequest:
    """One parsed timed-reminder request, ready to accept and ledger."""

    source_text: str
    offset_seconds: int
    task_phrase: str

    def due_at(self, now: int) -> int:
        return int(now) + int(self.offset_seconds)

    @property
    def is_short(self) -> bool:
        return 0 < int(self.offset_seconds) <= SHORT_TIMER_SECONDS

    def acceptance_text(self) -> str:
        offset = int(self.offset_seconds)
        if offset < 60:
            label = f"{offset}秒"
        elif offset % 3600 == 0:
            label = f"{offset // 3600}小时"
        elif offset % 60 == 0:
            label = f"{offset // 60}分钟"
        else:
            label = f"{offset}秒"
        return f"好嘞，{label}倒计时开始哦"

    def ledger_summary(self) -> str:
        task = (self.task_phrase or "约定事项").strip()
        if task.startswith("提醒"):
            return task[:240]
        return ("提醒" + task)[:240]

    def to_open_payload(self, *, now: int, reply_text: str) -> dict:
        quote = pick_acceptance_quote(reply_text)
        return {
            "action": "OPEN",
            "commitment_id": "",
            "summary": self.ledger_summary(),
            "evidence_quote": quote,
            "required_capability": "",
            "fulfillment_mode": "reminder",
            "due_at": self.due_at(now),
            "confidence": 0.99,
            "reason_code": "timed_reminder_heuristic",
            "extractor_version": "reminder-heuristic-v1",
        }


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or _DEFAULT_TZ))
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def _delay_from_groups(match: re.Match) -> Optional[int]:
    groups = match.groupdict()
    if groups.get("half"):
        unit = str(groups.get("half_unit") or "")
        if "分" in unit:
            return 30
        return 30 * 60
    try:
        amount = float(groups.get("num") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    unit = str(groups.get("unit") or "")
    if "时" in unit or "時" in unit:
        seconds = int(amount * 3600)
    elif "分" in unit:
        seconds = int(amount * 60)
    else:
        seconds = int(amount)
    if seconds <= 0 or seconds > MAX_OFFSET_SECONDS:
        return None
    return seconds


def _bound_offset_seconds(text: str) -> Optional[int]:
    source = str(text or "")
    bound = _BOUND_BEFORE.search(source) or _BOUND_AFTER.search(source)
    if bound is None:
        return None
    return _delay_from_groups(bound)


def parse_relative_offset_seconds(text: str) -> Optional[int]:
    """Parse a relative delay. Reminder-bound delays win over earlier numbers."""
    source = str(text or "")
    bound = _bound_offset_seconds(source)
    if bound is not None:
        return bound
    match = _RELATIVE_DELAY.search(source)
    if match is None:
        return None
    return _delay_from_groups(match)


def _parse_hour_token(raw: str) -> Optional[int]:
    token = str(raw or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
    else:
        value = _CN_HOUR.get(token)
        if value is None:
            return None
    if value == 24:
        return 0
    if 0 <= value <= 23:
        return value
    return None


def _parse_minute_token(raw: str) -> int:
    token = str(raw or "").strip()
    if not token:
        return 0
    if token == "半":
        return 30
    digits = re.sub(r"\D", "", token)
    if not digits:
        return 0
    value = int(digits)
    if 0 <= value <= 59:
        return value
    return 0


def _absolute_due_at(text: str, *, now: int, timezone_name: str) -> Optional[int]:
    match = _ABSOLUTE_TIME.search(str(text or ""))
    if match is None:
        return None
    day = str(match.group("day") or "")
    period = str(match.group("period") or match.group("period2") or "")
    hour = _parse_hour_token(match.group("hour") or match.group("hour2") or "")
    if hour is None:
        return None
    minute = _parse_minute_token(match.group("minute") or match.group("minute2") or "")
    if period in {"下午", "晚上", "傍晚"} and 1 <= hour <= 11:
        hour += 12
    elif period == "中午" and hour == 12:
        hour = 12
    elif period in {"早上", "上午", "凌晨"} and hour == 12:
        hour = 0
    if day in {"今晚"} and 1 <= hour <= 11:
        hour += 12
    zone = _zone(timezone_name)
    current = datetime.fromtimestamp(int(now or 0), zone)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day in {"明天", "明日", "明晚"}:
        target += timedelta(days=1)
    elif day in {"后天", "後天"}:
        target += timedelta(days=2)
    if target <= current:
        return None
    offset = int(target.timestamp()) - int(now)
    if offset <= 0 or offset > MAX_OFFSET_SECONDS:
        return None
    return int(target.timestamp())


def infer_timed_reminder_request(
    text: str,
    *,
    now: int = 0,
    timezone_name: str = _DEFAULT_TZ,
) -> Optional[TimedReminderRequest]:
    source = str(text or "")
    if not source.strip() or not _REQUEST_HINT.search(source):
        return None
    offset = _bound_offset_seconds(source)
    if offset is None:
        clock = int(now or 0)
        if clock <= 0:
            clock = int(datetime.now(_zone(timezone_name)).timestamp())
        absolute = _absolute_due_at(
            source, now=clock, timezone_name=timezone_name
        )
        if absolute is None:
            return None
        offset = int(absolute) - clock
    if offset is None or offset <= 0:
        return None
    return TimedReminderRequest(
        source_text=source,
        offset_seconds=int(offset),
        task_phrase=reminder_task_phrase(source),
    )


def infer_timed_reminder_from_topic(
    topic,
    *,
    now: int = 0,
    timezone_name: str = _DEFAULT_TZ,
) -> Optional[TimedReminderRequest]:
    latest = latest_user_message(topic)
    if latest is None:
        return None
    latest_text = str(getattr(latest, "text", "") or "")
    # Continuation keeps earlier turns in the window. A cancel must not be
    # stitched back onto「N分钟后提醒我」and re-parsed as a new request.
    if looks_like_reminder_cancel(latest_text):
        return None
    parsed = infer_timed_reminder_request(
        latest_text,
        now=now,
        timezone_name=timezone_name,
    )
    if parsed is not None:
        return parsed
    sender = str(getattr(latest, "sender_id", "") or "")
    chunks: list[str] = []
    for message in reversed(tuple(getattr(topic, "messages", ()) or ())):
        if getattr(message, "is_bot", False):
            continue
        if sender and str(getattr(message, "sender_id", "") or "") != sender:
            continue
        text = str(getattr(message, "text", "") or "").strip()
        if text:
            chunks.append(text)
        if len(chunks) >= 3:
            break
    if len(chunks) < 2:
        return None
    return infer_timed_reminder_request(
        " ".join(reversed(chunks)),
        now=now,
        timezone_name=timezone_name,
    )


def looks_like_timed_reminder_request(text: str) -> bool:
    source = str(text or "")
    if not source.strip() or not _REQUEST_HINT.search(source):
        return False
    if _bound_offset_seconds(source) is not None:
        return True
    return _ABSOLUTE_TIME.search(source) is not None


def looks_like_reminder_acceptance(text: str) -> bool:
    source = str(text or "")
    if not source.strip():
        return False
    if _ACCEPT_HINT.search(source):
        return True
    if parse_relative_offset_seconds(source) is None:
        return False
    return bool(re.search(r"(好的?|好嘞|好呀|行|嗯|可以|没问题|沒問題)", source))


def latest_user_message(topic) -> Optional[object]:
    """Last non-bot turn. Bot projections appended after a cancel must not hide it."""
    for message in reversed(tuple(getattr(topic, "messages", ()) or ())):
        if getattr(message, "is_bot", False):
            continue
        return message
    latest = getattr(topic, "latest", None)
    if latest is None or getattr(latest, "is_bot", False):
        return None
    return latest


def latest_user_text(topic) -> str:
    message = latest_user_message(topic)
    if message is None:
        return ""
    return str(getattr(message, "text", "") or "")


def looks_like_reminder_cancel(text: str, *, has_open_reminder: bool = False) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    if _CANCEL_STRONG.search(source):
        return True
    if not has_open_reminder:
        return False
    if _CANCEL_SOFT.search(source) and (
        "提醒" in source or "喊" in source or "倒计时" in source or "倒計時" in source
    ):
        return True
    if _CANCEL_SOFT.search(source) and len(source) <= 12:
        return True
    return False


def reminder_cancel_ack() -> str:
    return "好，那就不喊了"


def reminder_task_phrase(text: str) -> str:
    """Extract the remindable task body without the leading「提醒」."""
    cleaned = str(text or "")
    cleaned = _TIME_NOISE.sub(" ", cleaned)
    cleaned = _ABSOLUTE_NOISE.sub(" ", cleaned)
    cleaned = _REQUEST_NOISE.sub(" ", cleaned)
    cleaned = re.sub(r"[，,。.!！?？~～、\s]+", " ", cleaned).strip(" ：:|-")
    cleaned = " ".join(cleaned.split())
    if cleaned.startswith("提醒"):
        cleaned = cleaned[2:].strip()
    return cleaned[:240]


def reminder_task_from_summary(summary: str) -> str:
    task = " ".join(str(summary or "").split()).strip(" 。.!")
    if task.startswith("提醒"):
        task = task[2:].strip()
    return task


def looks_like_premature_reminder_delivery(
    *,
    user_text: str,
    reply_text: str,
) -> bool:
    """True when the bot delivers the reminder content instead of accepting the timer."""
    request = str(user_text or "")
    reply = " ".join(str(reply_text or "").split())
    if not looks_like_timed_reminder_request(request):
        return False
    if not reply:
        return False
    if looks_like_reminder_acceptance(reply):
        return False
    if _PREMATURE_DUE_PHRASE.search(reply):
        return True
    task = reminder_task_phrase(request)
    if not task:
        return False
    norm_reply = re.sub(r"[\s，,。.!！?？~～、]+", "", reply)
    norm_task = re.sub(r"[\s，,。.!！?？~～、]+", "", task)
    if not norm_task:
        return False
    if norm_reply in {norm_task, norm_task + "了"}:
        return True
    if norm_task in norm_reply and len(norm_reply) <= max(12, len(norm_task) + 4):
        return True
    return False


def acceptance_fallback_for_request(user_text: str) -> str:
    """Deterministic countdown acknowledgment when the model delivers too early."""
    parsed = infer_timed_reminder_request(user_text)
    if parsed is not None:
        return parsed.acceptance_text()
    offset = parse_relative_offset_seconds(user_text)
    if offset is None:
        return "好嘞，记下了，到时候喊你"
    return TimedReminderRequest(
        source_text=user_text,
        offset_seconds=offset,
        task_phrase=reminder_task_phrase(user_text),
    ).acceptance_text()


def reminder_summary_from_request(text: str) -> str:
    parsed = infer_timed_reminder_request(text)
    if parsed is not None:
        return parsed.ledger_summary()
    task = reminder_task_phrase(text) or "约定事项"
    if task.startswith("提醒"):
        return task[:240]
    return ("提醒" + task)[:240]


def pick_acceptance_quote(reply_text: str) -> str:
    delivered = " ".join(str(reply_text or "").split())
    if not delivered:
        return ""
    for pattern in (
        r".{0,12}倒计时开始.{0,8}",
        r".{0,12}倒計時開始.{0,8}",
        r".{0,12}提醒你.{0,12}",
        r".{0,12}到时候提醒.{0,8}",
        r".{0,12}到時候提醒.{0,8}",
    ):
        match = re.search(pattern, delivered)
        if match:
            quote = " ".join(match.group(0).split())
            if len(quote) >= 3 and quote in delivered:
                return quote[:180]
    return delivered[:180]


def infer_timed_reminder_commitment(
    *,
    user_text: str,
    reply_text: str,
    now: int,
    timezone_name: str = _DEFAULT_TZ,
) -> Optional[dict]:
    """Build an OPEN reminder payload when chat evidence is unambiguous."""
    request = infer_timed_reminder_request(
        str(user_text or ""),
        now=int(now),
        timezone_name=timezone_name,
    )
    reply = " ".join(str(reply_text or "").split())
    if request is None or not looks_like_reminder_acceptance(reply):
        return None
    payload = request.to_open_payload(now=int(now), reply_text=reply)
    if len(payload["evidence_quote"]) < 3 or payload["evidence_quote"] not in reply:
        return None
    if len(payload["summary"]) < 4:
        return None
    return payload


def recover_due_at(
    *,
    user_text: str,
    reply_text: str,
    now: int,
    timezone_name: str = _DEFAULT_TZ,
) -> Optional[int]:
    request = infer_timed_reminder_request(
        user_text,
        now=int(now),
        timezone_name=timezone_name,
    )
    if request is not None:
        return request.due_at(int(now))
    offset = parse_relative_offset_seconds(user_text) or parse_relative_offset_seconds(
        reply_text
    )
    if offset is None:
        return None
    return int(now) + int(offset)


def is_short_commitment(item, *, now: int = 0) -> bool:
    """True for reminder commitments whose original delay is within 15 minutes."""
    del now
    mode = str(getattr(item, "fulfillment_mode", "") or "")
    if mode != "reminder":
        return False
    created = int(getattr(item, "created_at", 0) or 0)
    due = int(getattr(item, "due_at", 0) or 0)
    if created > 0 and due > created:
        return (due - created) <= SHORT_TIMER_SECONDS
    return False
