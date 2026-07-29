"""爱弥斯 Persona Pack 加载与装配入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from ...core.context_assembly import AssembledPrompt, ContextAssembly
from ...core.response_act import ResponseActPlan
from ...core.relationships import RelationshipEntry
from ...core.session import GroupSession
from ...models import (
    MemoryItem,
    RelationshipState,
    ReplyMode,
    TargetingDecision,
    TopicSnapshot,
)
from .behavior_profile import (
    AEMEATH_PARTICIPATION_PROFILE,
    PersonaParticipationProfile,
)

PACK_DIR = Path(__file__).resolve().parent
CHARACTER_NAME = "爱弥斯"


class AemeathPersonaProvider:
    """人格层：Persona Pack + ContextAssembly。"""

    def __init__(
        self,
        relationships: Sequence[RelationshipEntry] = (),
        pack_dir: Optional[Path] = None,
    ) -> None:
        self._assembly = ContextAssembly(
            pack_dir=pack_dir or PACK_DIR,
            relationships=tuple(relationships),
            character_name=CHARACTER_NAME,
        )

    def set_relationships(self, relationships: Sequence[RelationshipEntry]) -> None:
        self._assembly.set_relationships(relationships)

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
        relationship_state: Optional[RelationshipState] = None,
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
            relationship_state=relationship_state,
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
        relationship_state: Optional[RelationshipState] = None,
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
            relationship_state=relationship_state,
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
