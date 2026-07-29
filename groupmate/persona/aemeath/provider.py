"""爱弥斯 Persona Pack 加载与装配入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from ...core.context_assembly import AssembledPrompt, ContextAssembly
from ...core.response_act import ResponseActPlan
from ...core.relationships import RelationshipEntry
from ...core.session import GroupSession
from ...models import MemoryItem, ReplyMode, TargetingDecision, TopicSnapshot
from .behavior_profile import (
    AEMEATH_PARTICIPATION_PROFILE,
    PersonaParticipationProfile,
)
from .relationships import DEFAULT_RELATIONSHIPS

PACK_DIR = Path(__file__).resolve().parent
CHARACTER_NAME = "爱弥斯"


class AemeathPersonaProvider:
    """人格层：Persona Pack + ContextAssembly。"""

    def __init__(
        self,
        override_prompt: str = "",
        relationships: Optional[Sequence[RelationshipEntry]] = None,
        pack_dir: Optional[Path] = None,
        group_brief: str = "",
        character_name: str = "",
    ) -> None:
        entries = (
            tuple(relationships) if relationships is not None else DEFAULT_RELATIONSHIPS
        )
        self.override_prompt = (override_prompt or "").strip()
        name = (character_name or "").strip() or CHARACTER_NAME
        self._assembly = ContextAssembly(
            pack_dir=pack_dir or PACK_DIR,
            relationships=entries,
            identity_override=self.override_prompt,
            character_name=name,
            group_brief=group_brief,
        )

    def set_relationships(self, relationships: Sequence[RelationshipEntry]) -> None:
        self._assembly.set_relationships(relationships)

    def set_group_brief(self, text: str) -> None:
        self._assembly.set_group_brief(text)

    def system_text(self) -> str:
        return self._assembly.build_system()

    async def system_prompt(self, group_id: str) -> str:
        del group_id
        return self._assembly.build_system()

    def build_user_context(
        self,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
        *,
        contribution: str = "",
        soft_trigger: bool = False,
        session: Optional[GroupSession] = None,
        mood_key: Optional[str] = None,
        favorability: Optional[int] = None,
        targeting: Optional[TargetingDecision] = None,
        reply_mode: Optional[ReplyMode] = None,
        response_act: Optional[ResponseActPlan] = None,
        capability_facts: Sequence[str] = (),
        capability_status: str = "",
    ) -> str:
        return self._assembly.build_user(
            topic,
            memories,
            contribution=contribution,
            soft_trigger=soft_trigger,
            session=session,
            mood_key=mood_key,
            favorability=favorability,
            targeting=targeting,
            reply_mode=reply_mode,
            response_act=response_act,
            capability_facts=capability_facts,
            capability_status=capability_status,
        )

    def assemble(
        self,
        topic: TopicSnapshot,
        memories: Sequence[MemoryItem],
        *,
        contribution: str = "",
        soft_trigger: bool = False,
        session: Optional[GroupSession] = None,
        mood_key: Optional[str] = None,
        favorability: Optional[int] = None,
        targeting: Optional[TargetingDecision] = None,
        reply_mode: Optional[ReplyMode] = None,
        response_act: Optional[ResponseActPlan] = None,
        capability_facts: Sequence[str] = (),
        capability_status: str = "",
    ) -> AssembledPrompt:
        return self._assembly.assemble(
            topic,
            memories,
            contribution=contribution,
            soft_trigger=soft_trigger,
            session=session,
            mood_key=mood_key,
            favorability=favorability,
            targeting=targeting,
            reply_mode=reply_mode,
            response_act=response_act,
            capability_facts=capability_facts,
            capability_status=capability_status,
        )

    @property
    def assembly(self) -> ContextAssembly:
        return self._assembly

    @property
    def participation_profile(self) -> PersonaParticipationProfile:
        """participation_profile（人格参与档案）：固定只读契约。"""

        return AEMEATH_PARTICIPATION_PROFILE
