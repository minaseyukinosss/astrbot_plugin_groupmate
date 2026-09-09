"""Detect a member asking the bot for stickers. No model on this path."""

from __future__ import annotations

import re
from dataclasses import dataclass


_COMMAND = re.compile(r"^\s*bq\b", re.IGNORECASE)
_REFUSE = re.compile(r"(别|不要|不准|别再).{0,8}(发|给|来).{0,8}(表情|图)")
_COMMENT = re.compile(
    r"(这个|那个|什么|刚才|刚刚).{0,6}表情|(发了|发过|发的).{0,6}表情"
)
_POLITE = r"(?:能不能|可不可以|可以不可以|可以|帮我|麻烦你|麻烦|求你|求|请)?"
_OPEN = (
    "再来|来点|来些|来张|发张|给张|要张|来个|发个|给个|来一张|发一张|给一张|"
    "给我|发我|丢我|整点|来|发|给|要|丢|整"
)
_STICKER = r"(?:表情包|表情)"
_TAIL = r"(?:呢|嘛|吗|呗|啊|呀|吧|啦|咯|哦|哈|看看|谢谢)?"
_ASK_FORWARD = re.compile(rf"{_POLITE}(?:{_OPEN})(.{{0,24}}?){_STICKER}{_TAIL}")
_ASK_BACKWARD = re.compile(
    rf"{_STICKER}(.{{0,12}}?)(?:来|发|给|要|呢|嘛|吗|呗|啊|呀|吧|看看|有吗|有没有)"
)
_ASK_HAVE = re.compile(
    rf"(?:有没有|有木有|还有没有){_STICKER}{_TAIL}"
    rf"|(?:有){_STICKER}(?:吗|嘛|没|没有)"
)
_ASK_BARE = re.compile(
    rf"{_POLITE}(?:来个|发个|给个|来张|发张|给张|来点|来些)(.{{1,16}}?)的{_TAIL}$"
)
_PACK = re.compile(
    r"(一堆|一包|一批|合集|打包|很多|好多|好几|几张|多[点些张]|多来|"
    r"来多|来些|来一堆|来几|十五张|15张|一打)"
)
_KIND_NOISE = re.compile(
    r"(?:能不能|可不可以|帮我|麻烦你|麻烦|求你|看看|谢谢|"
    r"再来|来点|来些|来张|发张|给张|要张|来个|发个|给个|给我|发我|丢我|"
    r"来一张|发一张|给一张|整点|"
    r"来|发|给|要|丢|整|点|些|张|个|的|了|一下|"
    r"一堆|一包|一批|合集|打包|很多|好多|几|两|一|"
    r"表情包|表情|十五张|15张|"
    r"呢|嘛|吗|呗|啊|呀|吧|啦|咯|哦|哈)"
)


@dataclass(frozen=True)
class StickerAsk:
    query: str
    pack: bool
    specified: bool


def parse_sticker_ask(text: str, *, addressed: bool) -> StickerAsk | None:
    if not addressed:
        return None
    body = str(text or "").strip()
    if not body or _COMMAND.search(body) is not None:
        return None
    compact = re.sub(r"[，。！？、,.!?\s]+", "", str(text or ""))
    if not compact or _REFUSE.search(compact) is not None:
        return None
    if _COMMENT.search(compact) is not None:
        return None
    middle, span = _kind_span(compact)
    if span is None:
        return None
    remainder = _KIND_NOISE.sub("", middle)
    specified = len(remainder) >= 2
    return StickerAsk(
        query=remainder if specified else span,
        pack=_PACK.search(compact) is not None,
        specified=specified,
    )


def _kind_span(compact: str) -> tuple[str, str] | tuple[None, None]:
    forward = _ASK_FORWARD.search(compact)
    if forward is not None:
        return str(forward.group(1) or ""), forward.group(0)
    backward = _ASK_BACKWARD.search(compact)
    if backward is not None:
        return str(backward.group(1) or ""), backward.group(0)
    have = _ASK_HAVE.search(compact)
    if have is not None:
        return "", have.group(0)
    bare = _ASK_BARE.search(compact)
    if bare is not None:
        return str(bare.group(1) or ""), bare.group(0)
    return None, None


__all__ = ("StickerAsk", "parse_sticker_ask")
