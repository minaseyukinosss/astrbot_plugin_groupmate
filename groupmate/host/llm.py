"""AstrBot LLM / 平台发送适配。"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

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
        user_prompt = getattr(plan, "user_prompt", "") or ""
        if not user_prompt:
            assemble = getattr(self.persona, "assemble", None)
            if assemble is not None:
                assembled = assemble(
                    topic,
                    memories,
                    contribution=plan.contribution,
                    soft_trigger=bool(getattr(plan, "soft_trigger", False)),
                )
                user_prompt = assembled.user
            else:
                user_prompt = "\n".join(
                    [
                        self.persona.build_user_context(topic, memories),
                        "<reply_task>",
                        "你可以补充：" + plan.contribution,
                        "只输出最终群聊回复或 <SILENCE>，不要解释过程。",
                        "</reply_task>",
                    ]
                )
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
    def __init__(self, context: Any, umo_getter: Callable[[str], str]) -> None:
        self.context = context
        self.umo_getter = umo_getter

    async def send_text(self, group_id: str, text: str, decision_id: str) -> None:
        del decision_id
        from astrbot.api.event import MessageChain

        chain = MessageChain().message(text)
        umo = self.umo_getter(group_id)
        sent = await self.context.send_message(umo, chain)
        if sent:
            return

        from astrbot.api.star import StarTools

        await StarTools.send_message_by_id(
            "GroupMessage",
            str(group_id),
            chain,
            platform="aiocqhttp",
        )

    async def send_segments(
        self,
        group_id: str,
        segments: Sequence[str],
        decision_id: str,
        quote_message_id: Optional[str] = None,
    ) -> None:
        del quote_message_id
        for segment in segments:
            text = str(segment or "").strip()
            if text:
                await self.send_text(group_id, text, decision_id)


class AstrBotPersonaProvider(AemeathPersonaProvider):
    def __init__(
        self,
        context: Any,
        persona_id: str = "",
        override_prompt: str = "",
        relationships: Optional[Sequence] = None,
        group_brief: str = "",
        character_name: str = "",
    ) -> None:
        super().__init__(
            override_prompt,
            relationships=relationships,
            group_brief=group_brief,
            character_name=character_name,
        )
        self.context = context
        self.persona_id = persona_id.strip()

    def _resolve_persona_text(self) -> str:
        if self.override_prompt:
            return self.override_prompt
        if not self.persona_id:
            return ""
        manager = getattr(self.context, "persona_manager", None)
        resolver = getattr(manager, "get_persona_v3_by_id", None)
        if not resolver:
            return ""
        persona = resolver(self.persona_id)
        if isinstance(persona, dict) and persona.get("prompt"):
            return str(persona["prompt"])
        if isinstance(persona, dict) and persona.get("system_prompt"):
            return str(persona["system_prompt"])
        return ""

    async def system_prompt(self, group_id: str) -> str:
        del group_id
        resolved = self._resolve_persona_text()
        if resolved:
            self._assembly.set_identity_override(resolved)
        return self._assembly.build_system()

    def assemble(self, topic, memories, **kwargs):
        resolved = self._resolve_persona_text()
        if resolved:
            self._assembly.set_identity_override(resolved)
        return super().assemble(topic, memories, **kwargs)
