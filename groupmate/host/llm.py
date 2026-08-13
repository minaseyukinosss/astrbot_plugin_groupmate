"""AstrBot LLM / 平台发送适配。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from ..models import (
    OutboundKind,
    OutboundSegment,
    SendResult,
)
from ..persona.aemeath import AemeathPersonaProvider


class AstrBotGenerationModel:
    def __init__(
        self,
        context: Any,
        provider_getter: Callable[[str], str],
        persona: AemeathPersonaProvider,
    ) -> None:
        self.context = context
        self.provider_getter = provider_getter
        self.persona = persona

    async def repair(self, text: str, violations: Sequence[str]) -> str:
        provider_id = self.provider_getter("")
        if not provider_id:
            return text
        codes = "、".join(str(item) for item in violations) or "style"
        lines = [
            "把下面的群聊回复改短、改自然，去掉客服腔、旁白和系统词。",
            "违规项：" + codes,
        ]
        if "premature_reminder_delivery" in {str(item) for item in violations}:
            lines.append(
                "对方是在约「以后再提醒」，不是现在就要提醒内容。"
                "改成角色自己的口吻，答应并确认时限开始；"
                "不要再说「交材料了/到时间了」，也不要套固定口头禅。"
            )
        lines.extend(
            [
                "只输出修改后的最终回复，不要解释。",
                "原文：",
                (text or "").strip(),
            ]
        )
        prompt = "\n".join(lines)
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt="你在帮群聊伙伴润色一句很短的回复。",
        )
        repaired = getattr(response, "completion_text", "") or ""
        return repaired.strip() or text

    async def generate(self, plan, topic, memories) -> str:
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id:
            raise RuntimeError("generation provider missing")
        user_prompt = plan.user_prompt or ""
        if not user_prompt:
            assembled = self.persona.assemble(
                topic,
                memories,
                contribution=plan.contribution,
                soft_trigger=bool(plan.soft_trigger),
            )
            user_prompt = assembled.user
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=user_prompt,
            system_prompt=plan.persona_prompt,
        )
        return getattr(response, "completion_text", "") or ""

    async def extract_relationship_evidence(
        self,
        *,
        topic,
        targeting,
        trigger,
        response_act,
        reply_text: str,
    ):
        """Propose one source-grounded social event; code performs final validation."""
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id or topic.latest is None:
            return None
        from ..engine.topics import select_active_messages

        active = select_active_messages(
            topic.messages,
            topic_created_at=topic.created_at,
            max_messages=6,
        )
        lines = []
        for message in active:
            role = "爱弥斯" if message.is_bot else (message.sender_name or "群成员")
            lines.append("[{}] {}".format(role, (message.text or "[非文本]")[:240]))
        prompt = "\n".join(
            [
                "只判断最新一位群成员对爱弥斯表现出的可观察关系事件。",
                "必须结合完整片段，不得按单个关键词判断，不得推断内心。",
                "普通聊天、普通称呼、观点不同、一次不明确玩笑都输出 NONE。",
                "允许 kind：THANKS、PRAISE、HELP_REQUEST、HELPED、"
                "FRIENDLY_TEASE、CORRECTION、BOUNDARY_PUSH、HARASSMENT、APOLOGY、NONE。",
                "evidence_quote 必须逐字来自最新消息，且只截取能支持判断的最短片段。",
                "只输出 JSON 对象："
                '{"kind":"NONE","confidence":0.0,'
                '"evidence_quote":"","reason_code":"no_clear_event"}',
                "当前触发：{}；回应动作：{}；对象依据：{}。".format(
                    getattr(trigger, "value", trigger),
                    getattr(getattr(response_act, "act", None), "value", ""),
                    ",".join(targeting.social_target.reason_codes),
                ),
                "群聊片段：",
                *lines,
                "爱弥斯本轮已发送：" + str(reply_text or "")[:240],
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "你是关系证据提取器。输出可审计 JSON，不输出解释、评价或隐藏推理。"
            ),
        )
        text = str(getattr(response, "completion_text", "") or "").strip()
        return self._first_json_object(text)

    async def extract_continuity_update(
        self,
        *,
        topic,
        targeting,
        open_items,
        reply_text: str,
    ):
        """Extract one auditable open-loop creation or resolution."""
        del targeting
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id or topic.latest is None:
            return None
        from ..engine.topics import select_active_messages

        active = select_active_messages(
            topic.messages,
            topic_created_at=topic.created_at,
            max_messages=6,
        )
        lines = []
        for message in active:
            role = "爱弥斯" if message.is_bot else (message.sender_name or "群成员")
            lines.append("[{}] {}".format(role, (message.text or "[非文本]")[:240]))
        open_lines = [
            "[{}] {}".format(item.item_id, item.summary[:200])
            for item in tuple(open_items or ())[:12]
        ] or ["无"]
        prompt = "\n".join(
            [
                "判断最新群成员消息是否形成或结束一件以后还需要接着聊的事。",
                "只有明确计划、承诺或需要后续追问的具体事项才能 OPEN。",
                "闲聊、愿望、情绪、猜测、泛泛的‘以后再说’都输出 NONE。",
                "成员请爱弥斯到点提醒（例如‘N分钟后提醒我交材料’）输出 NONE；那是提醒账本，不是相处跟进。",
                "COMPLETE 或 CANCEL 必须明确对应下方一个未完事项 item_id。",
                "evidence_quote 必须逐字来自最新消息；summary 用第三人称简短概括，不补充原文没有的事实。",
                "允许 action：OPEN、COMPLETE、CANCEL、NONE；kind：plan、promise、follow_up。",
                "due_at 只有原文能明确换算成 Unix 秒时填写，否则为 null。",
                "只输出 JSON 对象：",
                '{"action":"NONE","item_id":"","kind":"plan",'
                '"summary":"","evidence_quote":"","due_at":null,'
                '"confidence":0.0,"reason_code":"no_clear_continuity"}',
                "现有未完事项：",
                *open_lines,
                "群聊片段：",
                *lines,
                "爱弥斯本轮已发送：" + str(reply_text or "")[:240],
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "你是连续相处状态提取器。只输出可审计 JSON，不输出解释或隐藏推理。"
            ),
        )
        text = str(getattr(response, "completion_text", "") or "").strip()
        return self._first_json_object(text)

    async def extract_continuity_followup(self, *, topic, open_items):
        """Match the latest member message to one explicit open item."""
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id or topic.latest is None or not open_items:
            return None
        item_lines = [
            "[{}] {}；原话：{}".format(
                item.item_id,
                item.summary[:180],
                item.source_quote[:120],
            )
            for item in tuple(open_items or ())[:12]
        ]
        prompt = "\n".join(
            [
                "判断最新消息是否明确报告了下列某一件未完事项的新进展、完成或取消。",
                "必须是同一件具体事情；只共享考试、工作、照片等泛词不算相关。",
                "没有唯一对应项就输出 NONE。不得猜测弦外之音或补充原文没有的事实。",
                "只有对方主动明确报告重大进展、完成或取消，而且自然回应不会打断别人时，response_policy 才可为 speak；普通进度一律 observe。",
                "evidence_quote 必须逐字来自最新消息，并使用支持判断的最短片段。",
                "只输出 JSON 对象：",
                '{"action":"NONE","item_id":"","outcome":"progress",'
                '"response_policy":"observe","evidence_quote":"",'
                '"confidence":0.0,"reason_code":"no_unique_match"}',
                "允许 action：MATCH、NONE；outcome：progress、completed、cancelled。",
                "未完事项：",
                *item_lines,
                "最新消息：" + str(topic.latest.text or "")[:400],
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "你是群聊连续事项关联器。只输出可审计 JSON，不输出解释或隐藏推理。"
            ),
        )
        text = str(getattr(response, "completion_text", "") or "").strip()
        parsed = self._first_json_object(text)
        if not isinstance(parsed, dict) or str(parsed.get("action") or "").upper() != "MATCH":
            return None
        return parsed

    async def extract_self_commitment(
        self,
        *,
        topic,
        targeting,
        open_items,
        reply_text: str,
        capability_result,
        now: int = 0,
    ):
        """Extract one explicit commitment from the delivered Aemeath reply."""
        del targeting
        provider_id = self.provider_getter(topic.group_id)
        if not provider_id or topic.latest is None:
            return None
        capability_name = str(
            getattr(capability_result, "capability_name", "") or ""
        )
        capability_status = getattr(
            getattr(capability_result, "status", None), "value", ""
        )
        capability_facts = tuple(getattr(capability_result, "facts", ()) or ())
        open_lines = [
            "[{}] {}".format(item.commitment_id, item.summary[:200])
            for item in tuple(open_items or ())[:12]
        ] or ["无"]
        now_unix = int(now or 0)
        prompt = "\n".join(
            [
                "判断爱弥斯本轮已经发出的回复中，是否形成或更新一项自己的承诺。",
                "只有爱弥斯使用第一人称明确承担责任，且存在可判断的完成条件时才能 OPEN。",
                "礼貌回应、安慰、愿望、态度、‘有事叫我’、‘我记着’但没有具体事项，都输出 NONE。",
                "fulfillment_mode 只能是 reminder、capability、follow_up。"
                "只有明确约定到点提醒才是 reminder；需要已登记能力执行的是 capability；"
                "等待以后有事实再告知是 follow_up。",
                "若成员说‘N分钟后/小时后提醒我…’，且爱弥斯回复接受"
                "（如倒计时开始、到时候提醒、好嘞并重复时限），必须 OPEN，"
                "fulfillment_mode=reminder，due_at=当前Unix秒+相对秒数。",
                "当前Unix秒：{}。相对时间必须换算成绝对 due_at，禁止把 reminder 的 due_at 留空。"
                .format(now_unix or "未知"),
                "若承诺依赖查找、发送、修改或其他能力，required_capability 必须填写能力名；"
                "不知道准确能力名时输出 NONE。",
                "COMPLETE、BLOCK、WITHDRAW 必须明确对应下方一个 commitment_id。"
                "完成依赖能力的承诺时，本轮能力必须成功且名称一致。",
                "evidence_quote 必须逐字来自爱弥斯已发出的回复，summary 不得补充回复中没有的事实。",
                "summary 直接写要履行的事情，不要以‘爱弥斯会’开头；提醒类可写成‘提醒交材料’这种短句。",
                "允许 action：OPEN、COMPLETE、BLOCK、WITHDRAW、NONE。只输出 JSON 对象：",
                '{"action":"NONE","commitment_id":"","summary":"","evidence_quote":"",'
                '"required_capability":"","fulfillment_mode":"follow_up",'
                '"due_at":null,"confidence":0.0,'
                '"reason_code":"no_clear_commitment"}',
                "本轮能力：{}；状态：{}；已验证事实：{}。".format(
                    capability_name or "无",
                    capability_status or "未执行",
                    "；".join(str(item)[:160] for item in capability_facts) or "无",
                ),
                "现有未结束承诺：",
                *open_lines,
                "群成员最新消息：" + str(topic.latest.text or "")[:240],
                "爱弥斯本轮已发送：" + str(reply_text or "")[:400],
            ]
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            system_prompt=(
                "你是自我承诺审计提取器。只输出可审计 JSON，不输出解释或隐藏推理。"
            ),
        )
        text = str(getattr(response, "completion_text", "") or "").strip()
        return self._first_json_object(text)

    @staticmethod
    def _first_json_object(text: str):
        source = str(text or "").strip()
        start = source.find("{")
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(source[start:])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


class AstrBotVisionPort:
    def __init__(self, context: Any, provider_getter: Callable[[str], str]) -> None:
        self.context = context
        self.provider_getter = provider_getter

    async def describe(self, image_urls: Sequence[str]) -> str:
        provider_id = self.provider_getter("")
        if not provider_id:
            return ""
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt="用一句中文描述图片中与当前群聊有关的内容。",
            image_urls=list(image_urls),
        )
        return getattr(response, "completion_text", "") or ""


class AstrBotPlatformPort:
    def __init__(
        self,
        context: Any,
        umo_getter: Callable[[str], str],
        *,
        poke_interval_seconds: float = 0.45,
    ) -> None:
        self.context = context
        self.umo_getter = umo_getter
        self.poke_interval_seconds = max(0.0, float(poke_interval_seconds))

    async def send_outbound(
        self,
        group_id: str,
        segments: Sequence[OutboundSegment],
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ) -> SendResult:
        del decision_id
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Image, Plain, Reply

        try:
            from astrbot.api.message_components import At
        except ImportError:  # pragma: no cover - older AstrBot stubs
            At = None

        try:
            from astrbot.api.message_components import Face as FaceComponent
        except ImportError:  # pragma: no cover - older AstrBot stubs
            FaceComponent = None

        outbound = tuple(segments or ())
        if not outbound:
            return SendResult.failed("empty_outbound")

        poke_targets = [
            item.target_user_id
            for item in outbound
            if item.kind is OutboundKind.POKE and item.target_user_id
        ]
        content = [
            item
            for item in outbound
            if item.kind is not OutboundKind.POKE
        ]
        poke_error = ""
        for index, target in enumerate(poke_targets):
            if index > 0 and self.poke_interval_seconds > 0:
                await asyncio.sleep(self.poke_interval_seconds)
            poke_result = await self._send_poke(group_id, target)
            if poke_result.kind.value != "confirmed":
                poke_error = poke_result.error_code or "poke_failed"
                break

        if not content:
            if poke_targets and not poke_error:
                return SendResult.confirmed()
            return SendResult.failed(poke_error or "empty_outbound")

        chain = MessageChain()
        components = getattr(chain, "chain", None)
        if components is None:
            return SendResult.failed("message_chain_unsupported")
        if quote_message_id:
            components.append(Reply(id=str(quote_message_id)))
        try:
            for segment in content:
                if not isinstance(segment, OutboundSegment):
                    return SendResult.failed("invalid_outbound_segment")
                if segment.kind is OutboundKind.TEXT:
                    components.append(Plain(segment.text))
                    continue
                if segment.kind is OutboundKind.MENTION:
                    if At is None:
                        return SendResult.failed("mention_component_unavailable")
                    target = (
                        int(segment.target_user_id)
                        if str(segment.target_user_id).isdigit()
                        else segment.target_user_id
                    )
                    components.append(At(qq=target))
                    continue
                if segment.kind is OutboundKind.FACE:
                    if FaceComponent is None:
                        return SendResult.failed("face_component_unavailable")
                    face_id = (
                        int(segment.media_id)
                        if str(segment.media_id).isdigit()
                        else segment.media_id
                    )
                    components.append(FaceComponent(id=face_id))
                    continue
                media_ref = segment.media_ref
                if media_ref.startswith(("http://", "https://")):
                    components.append(Image.fromURL(media_ref))
                    continue
                media_path = Path(media_ref)
                if not media_path.is_absolute() or not media_path.is_file():
                    return SendResult.failed("invalid_media_ref")
                components.append(Image.fromFileSystem(str(media_path)))
        except Exception as exc:
            return SendResult.failed(
                "component_error", exc.__class__.__name__ + ":" + str(exc)
            )
        text_result = await self._send_chain(group_id, chain)
        if text_result.kind.value == "confirmed":
            return text_result
        if poke_targets and not poke_error:
            # Poke already went out; keep partial success as confirmed text failure detail.
            return SendResult.confirmed()
        return text_result

    async def _send_poke(self, group_id: str, target_user_id: str) -> SendResult:
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return SendResult.failed("poke_client_unavailable")
        call = getattr(client, "call_action", None)
        if not callable(call):
            return SendResult.failed("poke_send_failed")
        user_id = (
            int(target_user_id) if str(target_user_id).isdigit() else target_user_id
        )
        group_value = int(group_id) if str(group_id).isdigit() else group_id
        attempts = (
            {"user_id": user_id, "group_id": group_value},
            {"user_id": user_id},
        )
        for action in ("group_poke", "send_poke", "friend_poke"):
            for payload in attempts:
                try:
                    await call(action, **payload)
                    return SendResult.confirmed()
                except Exception:
                    continue
        return SendResult.failed("poke_send_failed")

    def _resolve_aiocqhttp_client(self) -> Any:
        context = self.context
        for attr in ("get_platform", "get_platform_inst"):
            getter = getattr(context, attr, None)
            if not callable(getter):
                continue
            try:
                platform = getter("aiocqhttp")
            except Exception:
                platform = None
            client = getattr(platform, "get_client", None)
            if callable(client):
                try:
                    return client()
                except Exception:
                    pass
            bot = getattr(platform, "bot", None)
            if bot is not None:
                return bot
        platforms = getattr(context, "platforms", None) or getattr(
            context, "platform_manager", None
        )
        values: List[Any] = []
        if isinstance(platforms, dict):
            values = list(platforms.values())
        elif platforms is not None:
            get_insts = getattr(platforms, "get_insts", None)
            if callable(get_insts):
                try:
                    values = list(get_insts() or ())
                except Exception:
                    values = []
        for platform in values:
            name = str(getattr(getattr(platform, "meta", lambda: None)(), "name", "") or "")
            if not name:
                meta = getattr(platform, "metadata", None) or getattr(
                    platform, "meta_data", None
                )
                name = str(getattr(meta, "name", "") or "")
            if name and name.casefold() != "aiocqhttp":
                continue
            client = getattr(platform, "get_client", None)
            if callable(client):
                try:
                    return client()
                except Exception:
                    pass
            bot = getattr(platform, "bot", None)
            if bot is not None:
                return bot
        return None

    async def _send_chain(self, group_id: str, chain: Any) -> SendResult:
        umo = self.umo_getter(group_id)
        try:
            sent = await self.context.send_message(umo, chain)
        except Exception as exc:
            return SendResult.failed(
                "send_error", exc.__class__.__name__ + ":" + str(exc)
            )
        if sent:
            return SendResult.confirmed()

        from astrbot.api.star import StarTools

        send = getattr(StarTools, "send_message", None)
        if not callable(send):
            return SendResult.failed("platform_unavailable")
        try:
            receipt = await send(umo, chain)
        except Exception as exc:
            return SendResult.failed(
                "fallback_error", exc.__class__.__name__ + ":" + str(exc)
            )
        if receipt:
            return SendResult.confirmed()
        return SendResult.failed("platform_unavailable")
