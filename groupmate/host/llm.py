"""AstrBot LLM / 平台发送适配。"""

from __future__ import annotations

import asyncio
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
        prompt = "\n".join(
            [
                "把下面的群聊回复改短、改自然，去掉客服腔、旁白和系统词。",
                "违规项：" + codes,
                "只输出修改后的最终回复，不要解释。",
                "原文：",
                (text or "").strip(),
            ]
        )
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
        sent = await self.context.send_message(umo, chain)
        if sent:
            return SendResult.confirmed()

        from astrbot.api.star import StarTools

        try:
            receipt = await StarTools.send_message_by_id(
                "GroupMessage",
                str(group_id),
                chain,
                platform="aiocqhttp",
            )
        except Exception as exc:
            return SendResult.failed(
                "fallback_error", exc.__class__.__name__ + ":" + str(exc)
            )
        if receipt is True:
            return SendResult.confirmed()
        return SendResult.unknown("fallback_without_receipt")
