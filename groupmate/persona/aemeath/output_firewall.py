"""爱弥斯出戏/输出防火墙（OutputGuard 实现）。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Sequence

from ...models import ReplyMode
from ...ports import GuardResult
from ...core.intent import constraints_for
from ...core.response_act import ResponseAct


class AemeathOutputFirewall:
    # 任意括号动作/舞台旁白（不只拦「不回复」类决策旁白）
    _STAGE_DIRECTION = re.compile(
        r"[（(][^）)\n]{1,}[）)]"
        r"|[\*＊][^*\n＊]{2,}[\*＊]"
    )
    _NARRATION = re.compile(
        r"[（(][^）)]*(?:不回复|沉默|没人叫|系统|决定|思考|旁白)[^）)]*[）)]",
        re.IGNORECASE,
    )
    _CUSTOMER_SERVICE = re.compile(
        r"(?:有什么(?:想聊|可以帮)|需要我帮|有什么事需要|请问需要|小爱在呢)",
        re.IGNORECASE,
    )
    _SYSTEM_VOCABULARY = re.compile(
        r"(?:prompt|system\s*prompt|模型输出|插件配置|人格调参|系统决定)",
        re.IGNORECASE,
    )
    _FORCED_FOLLOWUP = re.compile(
        r"(?:(?:你呢|那你呢|然后呢)[\s？?。！!~～.…]*$"
        r"|(?:^|[，,。！？!?；;：:.…\r\n])[^\S\r\n]*(?:怎么啦|怎么了)"
        r"[\s？?。！!~～.…]*$"
        r"|有什么想聊)",
        re.IGNORECASE,
    )
    _INTERNAL_ID = re.compile(r"(?:sender_id|user_id|internal_id|内部ID)", re.IGNORECASE)
    _COMPLETION_CLAIM = re.compile(
        r"(?:(?:已经|已|刚刚).{0,16}"
        r"(?:完成(?:了)?|成功(?:了)?|搞定(?:了)?|好(?:了)?))"
        r"|(?:搞定了|完成了|做好了|弄好了|查好了|处理好了|发布成功)",
        re.IGNORECASE,
    )
    _LEADING_MONO_INTERJECTION = re.compile(
        r"^(?:嘿|噗|呵|哎|唉)[，,～~\s…]"
    )
    _DECORATIVE_PUNCT = re.compile(r"[～~]{1,}|——|…{2,}")
    _POKE_BYSTANDER_VOICE = re.compile(r"(?:你俩|你们俩|你们两个)")

    def validate(
        self,
        text: str,
        recent_outputs: Sequence[str],
        *,
        reply_mode: Optional[ReplyMode] = None,
        response_act: Optional[ResponseAct] = None,
        capability_status=None,
    ) -> GuardResult:
        cleaned = (text or "").strip().strip("`").strip()
        codes: List[str] = []
        non_repairable = set()
        constr = constraints_for(reply_mode or ReplyMode.SHORT_SOCIAL)
        max_chars = constr.max_chars
        max_sentences = constr.max_sentences

        if not cleaned:
            codes.append("empty_output")
            non_repairable.add("empty_output")
        if len(cleaned) > max_chars:
            codes.append("too_long")
        if self._unit_count(cleaned) > max_sentences:
            codes.append("too_many_sentences")
        if self._STAGE_DIRECTION.search(cleaned) or self._NARRATION.search(cleaned):
            codes.append("decision_narration")
        if self._CUSTOMER_SERVICE.search(cleaned):
            codes.append("customer_service_template")
        if self._SYSTEM_VOCABULARY.search(cleaned):
            codes.append("system_vocabulary")
        if self._FORCED_FOLLOWUP.search(cleaned):
            codes.append("forced_followup")
        if self._INTERNAL_ID.search(cleaned):
            codes.append("internal_id_leak")
            non_repairable.add("internal_id_leak")
        if self._LEADING_MONO_INTERJECTION.search(cleaned):
            codes.append("leading_mono_interjection")
        if self._DECORATIVE_PUNCT.search(cleaned):
            codes.append("decorative_punctuation")
        if (
            response_act is ResponseAct.PLAYFUL_REPLY
            and self._POKE_BYSTANDER_VOICE.search(cleaned)
        ):
            codes.append("false_bystander_voice")
        status = getattr(capability_status, "value", capability_status)
        status = str(status or "").strip().lower()
        task_not_successful = (
            response_act is ResponseAct.TASK_UNSUPPORTED
            or (
                response_act is ResponseAct.TASK_HANDOFF
                and status != "success"
            )
            or (bool(status) and status != "success")
        )
        if task_not_successful and self._COMPLETION_CLAIM.search(cleaned):
            codes.append("false_task_completion")
        if self._is_duplicate(cleaned, recent_outputs):
            codes.append("duplicate_output")
            non_repairable.add("duplicate_output")

        unique_codes = tuple(dict.fromkeys(codes))
        return GuardResult(
            accepted=not unique_codes,
            text=cleaned,
            codes=unique_codes,
            repairable=bool(unique_codes) and not bool(non_repairable),
        )

    @classmethod
    def _unit_count(cls, text: str) -> int:
        """按句读或换行气泡计条数（防无标点长段/多行小说腔）。"""
        by_punct = [c for c in re.split(r"[。！？!?]+", text) if c.strip()]
        by_line = [c for c in text.splitlines() if c.strip()]
        return max(len(by_punct), len(by_line))

    def _is_duplicate(self, text: str, recent_outputs: Sequence[str]) -> bool:
        normalized = self._normalize(text)
        if not normalized:
            return False
        for previous in recent_outputs[-20:]:
            candidate = self._normalize(previous)
            if candidate and SequenceMatcher(None, normalized, candidate).ratio() >= 0.92:
                return True
        return False

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？~～]+", "", (text or "").lower())
