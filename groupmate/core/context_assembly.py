"""ContextAssembly：稳定 system / 动态 user 唯一装配顺序。"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from ..models import MemoryItem, ReplyMode, TargetingDecision, TopicSnapshot
from .history_format import (
    focus_speaker,
    format_history_block,
    format_relationship_line,
    select_active_messages,
)
from .intent import constraints_for, speak_note_for
from .mood import describe_mood, infer_mood, load_mood_descriptions
from .relationships import RelationshipEntry, relationship_map, resolve_speaker
from .response_act import ResponseAct, ResponseActPlan
from .self_episodes import format_self_episodes, needs_self_recall
from .session import DialogueTurn, GroupSession
from .voice_anchor import format_voice_anchor_block, load_voice_anchor


ROLE_PLAYING_START = """## 角色扮演
接下来你将**完全扮演**下面的角色。
- 禁止出戏
- 禁止承认自己是 AI / 语言模型 / 机器人
- 禁止使用客服式、主持式回复
- 严格遵循角色性格与说话方式
"""

# 动态 user 块顺序（测试锁定；改动须同步规格）
DYNAMIC_BLOCK_ORDER = (
    "recent_messages",
    "session_turns",
    "mood",
    "relationship_line",
    "voice_anchor",
    "self_episodes",
    "relevant_memories",
    "memory_guide",
    "response_act",
    "reply_mode",
    "speak_note",
    "reply_task",
)


@dataclass(frozen=True)
class AssembledPrompt:
    """一次生成的完整装配结果。"""

    system: str
    user: str
    soft_trigger: bool
    mood_key: str = "neutral"


class ContextAssembly:
    """
    慢变进 system，快变进 user。

    SYSTEM：
      ROLE_START → persona.md → constraints → group_brief → 收尾人格钉
      （不含 mood / per-user 关系）

    USER（固定顺序，见 DYNAMIC_BLOCK_ORDER）：
      history → session → mood → relationship_line → voice_anchor
      → self_episodes → memories → memory_guide → speak_note → reply_task
    """

    def __init__(
        self,
        pack_dir: Path,
        relationships: Sequence[RelationshipEntry] = (),
        identity_override: str = "",
        character_name: str = "角色",
        group_brief: str = "",
    ) -> None:
        self.pack_dir = Path(pack_dir)
        self.character_name = character_name.strip() or "角色"
        self.identity_override = (identity_override or "").strip()
        self.group_brief = (group_brief or "").strip()
        self._relationships: Dict[str, Tuple[str, str]] = relationship_map(
            tuple(relationships)
        )
        self._identity_cache: Optional[str] = None
        self._constraints_cache: Optional[str] = None
        self._voice_cache: Dict[str, str] = {}
        self._mood_descriptions = load_mood_descriptions(self.pack_dir)
        self._memory_guide_cache: Optional[str] = None

    def set_relationships(self, relationships: Sequence[RelationshipEntry]) -> None:
        self._relationships = relationship_map(tuple(relationships))

    def set_identity_override(self, text: str) -> None:
        self.identity_override = (text or "").strip()
        self._identity_cache = None

    def set_group_brief(self, text: str) -> None:
        self.group_brief = (text or "").strip()

    def identity_text(self) -> str:
        if self.identity_override:
            return self.identity_override
        if self._identity_cache is None:
            path = self.pack_dir / "persona.md"
            self._identity_cache = (
                path.read_text(encoding="utf-8").strip() if path.exists() else ""
            )
        return self._identity_cache

    def constraints_text(self) -> str:
        if self._constraints_cache is None:
            path = self.pack_dir / "constraints.md"
            if path.exists():
                self._constraints_cache = path.read_text(encoding="utf-8").strip()
            else:
                self._constraints_cache = ""
        return self._constraints_cache

    def memory_guide_text(self) -> str:
        if self._memory_guide_cache is None:
            path = self.pack_dir / "memory_guide.md"
            if path.exists():
                self._memory_guide_cache = path.read_text(encoding="utf-8").strip()
            else:
                self._memory_guide_cache = ""
        return self._memory_guide_cache

    def build_system(self) -> str:
        parts = [
            ROLE_PLAYING_START.strip(),
            self.identity_text(),
        ]
        constraints = self.constraints_text()
        if constraints:
            parts.append(constraints)
        if self.group_brief:
            parts.append("## 当前群氛围\n" + self.group_brief)
        parts.append(
            "\n".join(
                [
                    "---",
                    "## 最后提醒（最高权重）",
                    f"你首先是「{self.character_name}」这个活生生的人，其次才是会回话的伙伴。",
                    "- 短句、口语、一次一个重点——这是本能，不是表演",
                    "- 不确定该不该说时，输出 <SILENCE>",
                    "- 绝不暴露模型、系统、插件、决策过程",
                ]
            )
        )
        return "\n\n".join(part for part in parts if part).strip()

    def build_user(
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
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        sender_id, sender_name = self._focus_from_targeting(active, targeting)
        ambiguous = bool(
            targeting is not None
            and targeting.social_target.kind.value == "ambiguous"
        )
        _, relationship, _ = resolve_speaker(
            sender_id, sender_name, self._relationships
        )
        latest_text = active[-1].text if active else ""
        resolved_mood = mood_key or infer_mood(
            soft_trigger=soft_trigger,
            latest_text=latest_text,
            relationship="" if ambiguous else relationship,
            favorability=None if ambiguous else favorability,
        )
        mode = reply_mode or ReplyMode.SHORT_SOCIAL
        mode_constr = constraints_for(mode)

        sections = ["<group_context>"]

        history = format_history_block(active, self._relationships)
        if history:
            sections.append(history)

        session_lines = self._format_session_turns(
            session.recent_turns(6) if session is not None else ()
        )
        if session_lines:
            sections.extend(
                ["<session_turns>", "\n".join(session_lines), "</session_turns>"]
            )

        sections.append(
            "<mood>{}</mood>".format(describe_mood(resolved_mood, self._mood_descriptions))
        )

        if sender_id or sender_name:
            sections.append(
                "<relationship_line>{}</relationship_line>".format(
                    format_relationship_line(
                        sender_id,
                        sender_name,
                        self._relationships,
                        favorability=None if ambiguous else favorability,
                        allow_intimate_address=not ambiguous,
                    )
                )
            )

        anchor = load_voice_anchor(self.pack_dir, self._voice_cache)
        sections.append(format_voice_anchor_block(anchor, self.character_name))

        if needs_self_recall(latest_text):
            episodes = format_self_episodes(session)
            if episodes:
                sections.append(episodes)

        memory_lines = []
        for item in memories[:8]:
            text = html.escape(item.text[:300])
            mid = (item.memory_id or "").strip()
            if mid:
                memory_lines.append("[{}] {}".format(html.escape(mid[:8]), text))
            else:
                memory_lines.append(text)
        if memory_lines:
            sections.extend(
                ["<relevant_memories>", "\n".join(memory_lines), "</relevant_memories>"]
            )
            guide = self.memory_guide_text()
            if guide:
                sections.append(
                    "<memory_guide>{}</memory_guide>".format(html.escape(guide[:500]))
                )

        response_block = self._response_act_block(
            response_act,
            capability_facts,
            capability_status,
        )
        if response_block:
            sections.append(response_block)

        sections.append(
            "<reply_mode>{}；{}</reply_mode>".format(
                html.escape(mode.value),
                html.escape(mode_constr.length_hint),
            )
        )
        sections.extend(
            [
                "<speak_note>{}</speak_note>".format(
                    speak_note_for(mode, soft_trigger=soft_trigger)
                ),
                "<reply_task>你可以补充：{}。只输出最终群聊回复或 <SILENCE>，不要解释过程。</reply_task>".format(
                    html.escape((contribution or "给一句自然短反应").strip())
                ),
                "</group_context>",
            ]
        )
        return "\n".join(sections)

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
        active = select_active_messages(
            topic.messages, topic_created_at=topic.created_at
        )
        sender_id, sender_name = self._focus_from_targeting(active, targeting)
        ambiguous = bool(
            targeting is not None
            and targeting.social_target.kind.value == "ambiguous"
        )
        _, relationship, _ = resolve_speaker(
            sender_id, sender_name, self._relationships
        )
        latest_text = active[-1].text if active else ""
        resolved_mood = mood_key or infer_mood(
            soft_trigger=soft_trigger,
            latest_text=latest_text,
            relationship="" if ambiguous else relationship,
            favorability=None if ambiguous else favorability,
        )
        return AssembledPrompt(
            system=self.build_system(),
            user=self.build_user(
                topic,
                memories,
                contribution=contribution,
                soft_trigger=soft_trigger,
                session=session,
                mood_key=resolved_mood,
                favorability=favorability,
                targeting=targeting,
                reply_mode=reply_mode,
                response_act=response_act,
                capability_facts=capability_facts,
                capability_status=capability_status,
            ),
            soft_trigger=soft_trigger,
            mood_key=resolved_mood,
        )

    @staticmethod
    def _response_act_block(
        plan: Optional[ResponseActPlan],
        capability_facts: Sequence[str],
        capability_status: str,
    ) -> str:
        if plan is None:
            return ""
        status = str(capability_status or "").strip().lower()
        lines = [
            "<response_act>",
            "<act>{}</act>".format(html.escape(plan.act.value)),
        ]
        if status == "success":
            facts = []
            for fact in capability_facts or ():
                cleaned = " ".join(str(fact or "").split())[:500]
                if cleaned:
                    facts.append(
                        "<fact>{}</fact>".format(html.escape(cleaned))
                    )
            if facts:
                lines.extend(["<capability_facts>"] + facts + ["</capability_facts>"])
        if (
            plan.act is ResponseAct.TASK_UNSUPPORTED
            or (
                plan.act is ResponseAct.TASK_HANDOFF
                and status != "success"
            )
            or (status and status != "success")
        ):
            lines.append(
                "<truth_constraint>任务尚未成功；不得声称已完成，"
                "不得编造结果。</truth_constraint>"
            )
        lines.append("</response_act>")
        return "\n".join(lines)

    @staticmethod
    def _focus_from_targeting(
        active,
        targeting: Optional[TargetingDecision],
    ) -> Tuple[str, str]:
        if targeting is not None:
            audience = targeting.reply_audience
            if audience.kind.value == "user" and audience.target_user_ids:
                uid = audience.target_user_ids[0]
                for message in reversed(tuple(active)):
                    if message.sender_id == uid:
                        return uid, message.sender_name or ""
                return uid, ""
            if audience.kind.value == "ambiguous":
                return "", ""
        return focus_speaker(active)

    @staticmethod
    def _format_session_turns(turns: Sequence[DialogueTurn]) -> list:
        lines = []
        for turn in turns:
            role = "assistant" if turn.role == "assistant" else "user"
            lines.append(
                '<turn role="{}" speaker="{}">{}</turn>'.format(
                    role,
                    html.escape(turn.speaker[:80]),
                    html.escape(turn.text[:300]),
                )
            )
        return lines
