"""Durable, text-only reply planning and generation for the chat mainline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping, Protocol

from .actions.contracts import (
    DeliveryBundle,
    DeliveryPart,
    DeliveryPartKind,
    OutboxPart,
)
from .actions.generation import GeneratedDraft, GenerationRequest, OutputFirewall
from .actions.style import StyleDirective
from .contracts import SocialEventEnvelope
from .delivery.outbox import OutboxService
from .expression import ExpressionPlan, ExpressionPlanner
from .persistence.schema import connect_database, initialize_database
from .persona.canon import PersonaCanon
from .society.relationships import PublicAffection, RelationshipStage


class ReplyPlanIdentityConflict(RuntimeError):
    """Raised when a durable reply identity is reused for different content."""


class TextModelPort(Protocol):
    async def complete_text(self, *, system_prompt: str, prompt: str) -> str: ...


@dataclass(frozen=True)
class ReplyPlan:
    plan_id: str
    correlation_id: str
    persona_id: str
    group_id: str
    scene_version: int
    config_version: int
    platform: str
    platform_id: str
    session: str | None
    bot_id: str | None
    target_id: str | None
    topic_id: str | None
    evidence_event_ids: tuple[str, ...]
    intention_id: str
    act: str
    required: bool
    style: StyleDirective
    created_at: int
    expires_at: int
    expression: ExpressionPlan
    status: str = "planned"
    participation_lane: str = "AMBIENT"
    member_context: str = ""


@dataclass(frozen=True)
class ReplyPreview:
    text: str | None
    status: str
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"READY", "MODEL_FAILED", "REJECTED"}:
            raise ValueError("unknown reply preview status")
        if self.status == "READY" and not str(self.text or "").strip():
            raise ValueError("ready reply preview requires text")


@dataclass(frozen=True)
class ReplyExecutionResult:
    part: OutboxPart | None
    status: str
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"READY", "MODEL_FAILED", "REJECTED"}:
            raise ValueError("unknown reply execution status")
        if self.status == "READY" and self.part is None:
            raise ValueError("ready reply execution requires an outbox part")

    @property
    def usable_for_lease(self) -> bool:
        return self.status == "READY" and self.part is not None


class ReplyPlanRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        with connect_database(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reply_plans ("
                "plan_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL UNIQUE, "
                "persona_id TEXT NOT NULL, group_id TEXT NOT NULL, "
                "plan_json TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reply_plans_status "
                "ON reply_plans(status, expires_at)"
            )

    def save(self, plan: ReplyPlan) -> ReplyPlan:
        encoded = self._encode(plan)
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT plan_json FROM reply_plans WHERE plan_id=?",
                (plan.plan_id,),
            ).fetchone()
            if row is not None:
                existing = self._decode(str(row[0]))
                if existing != plan:
                    raise ReplyPlanIdentityConflict(plan.plan_id)
                return existing
            try:
                db.execute(
                    "INSERT INTO reply_plans(plan_id, correlation_id, persona_id, "
                    "group_id, plan_json, status, created_at, expires_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan.correlation_id,
                        plan.persona_id,
                        plan.group_id,
                        encoded,
                        plan.status,
                        plan.created_at,
                        plan.expires_at,
                    ),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ReplyPlanIdentityConflict(plan.plan_id) from exc
                raise
        return plan

    def load(self, plan_id: str) -> ReplyPlan:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT plan_json FROM reply_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
        if row is None:
            raise LookupError(plan_id)
        return self._decode(str(row[0]))

    def by_correlation(self, correlation_id: str) -> ReplyPlan:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT plan_json FROM reply_plans WHERE correlation_id=?",
                (correlation_id,),
            ).fetchone()
        if row is None:
            raise LookupError(correlation_id)
        return self._decode(str(row[0]))

    def mark(self, plan_id: str, status: str) -> ReplyPlan:
        current = self.load(plan_id)
        updated = replace(current, status=str(status))
        encoded = self._encode(updated)
        with connect_database(self.path) as db:
            changed = db.execute(
                "UPDATE reply_plans SET plan_json=?, status=? WHERE plan_id=?",
                (encoded, updated.status, plan_id),
            ).rowcount
        if changed != 1:
            raise LookupError(plan_id)
        return updated

    def authorizes_bundle(self, bundle: DeliveryBundle) -> bool:
        try:
            plan = self.by_correlation(bundle.correlation_id)
        except LookupError:
            return False
        return bool(
            plan.persona_id == bundle.persona_id
            and plan.group_id == bundle.group_id
            and plan.topic_id == bundle.topic_id
            and plan.created_at <= bundle.created_at
            and bundle.expires_at <= plan.expires_at
            and plan.status in {"planned", "generated", "enqueued"}
        )

    @staticmethod
    def _encode(plan: ReplyPlan) -> str:
        return json.dumps(
            asdict(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _decode(encoded: str) -> ReplyPlan:
        values = json.loads(encoded)
        values["evidence_event_ids"] = tuple(values["evidence_event_ids"])
        values["style"] = StyleDirective(**values["style"])
        values.setdefault("participation_lane", "AMBIENT")
        expression = values.get("expression")
        values["expression"] = (
            ExpressionPlan(**expression)
            if isinstance(expression, Mapping)
            else ExpressionPlan.conservative()
        )
        return ReplyPlan(**values)


class ReplyPlanner:
    def __init__(self, expression_planner: ExpressionPlanner | None = None) -> None:
        self._expression_planner = expression_planner or ExpressionPlanner()

    def plan(
        self,
        evaluation: object,
        *,
        now: int,
        persona_profile: Mapping[str, object],
        relationship: PublicAffection | None = None,
        recent_outputs: tuple[str, ...] = (),
        relationship_memory_cues: tuple[str, ...] = (),
        member_context: str = "",
    ) -> ReplyPlan | None:
        frame = getattr(evaluation, "frame", None)
        governor = getattr(evaluation, "governor_result", None)
        if (
            not getattr(evaluation, "accepted", False)
            or frame is None
            or governor is None
            or governor.outcome != "ACT"
            or len(governor.selected_intention_ids) != 1
        ):
            return None
        intention_id = governor.selected_intention_ids[0]
        selected = next(
            (
                item
                for item in getattr(evaluation, "candidates", ())
                if item.intention_id == intention_id
            ),
            None,
        )
        if selected is None or selected.expires_at <= int(now):
            return None
        expression = self._expression_planner.plan(
            lane=str(getattr(evaluation, "participation_lane", "AMBIENT")),
            act=selected.proposed_act,
            source_text=str(evaluation.source_event.payload.get("text") or ""),
            persona_profile=persona_profile,
            relationship=relationship
            or PublicAffection(0.0, RelationshipStage.STRANGER),
            recent_outputs=tuple(recent_outputs),
            relationship_memory_cues=tuple(relationship_memory_cues),
        )
        return self._build_plan(
            evaluation=evaluation,
            frame=frame,
            selected=selected,
            intention_id=intention_id,
            expression=expression,
            member_context=str(member_context)[:1200],
            now=int(now),
        )

    def _build_plan(
        self,
        *,
        evaluation: object,
        frame: object,
        selected: object,
        intention_id: str,
        expression: ExpressionPlan,
        member_context: str,
        now: int,
    ) -> ReplyPlan:
        source = evaluation.source_event
        payload = source.payload
        target_id = selected.target_id or self._first(frame.candidate_audiences)
        topic_id = selected.topic_id or self._first(frame.focus_topic_ids)
        required = frame.trigger_kind == "FAST"
        identity = f"{frame.frame_id}:{intention_id}:{source.correlation_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return ReplyPlan(
            plan_id=f"reply:{digest}",
            correlation_id=source.correlation_id,
            persona_id=evaluation.persona_id,
            group_id=str(source.group_id),
            scene_version=evaluation.scene_version,
            config_version=evaluation.config_version,
            platform=str(payload.get("platform") or "qq"),
            platform_id=str(payload.get("platform_id") or "qq"),
            session=self._optional(payload.get("session")),
            bot_id=self._optional(payload.get("bot_id")),
            target_id=target_id,
            topic_id=topic_id,
            evidence_event_ids=tuple(selected.evidence_event_ids),
            intention_id=intention_id,
            act=selected.proposed_act,
            required=required,
            style=StyleDirective(
                mode="social",
                act=selected.proposed_act,
                posture="friendly",
                address=None,
                max_chars=120,
                max_sentences=3,
                max_segments=2,
                warmth=55,
                playfulness=15 if selected.kind == "PLAY" else 0,
                directness=75,
                particle_budget=1,
                punctuation_budget=2,
                media_policy="text_only",
                avoid_patterns=(),
            ),
            created_at=now,
            expires_at=min(int(selected.expires_at), now + 30),
            expression=expression,
            participation_lane=str(
                getattr(evaluation, "participation_lane", "AMBIENT")
                or "AMBIENT"
            ),
            member_context=str(member_context)[:1200],
        )

    @staticmethod
    def _first(values: tuple[str, ...]) -> str | None:
        return values[0] if values else None

    @staticmethod
    def _optional(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None


class ReplyExecutor:
    def __init__(
        self,
        repository: ReplyPlanRepository,
        outbox: OutboxService,
        model: TextModelPort,
        *,
        firewall: OutputFirewall | None = None,
    ) -> None:
        self.repository = repository
        self.outbox = outbox
        self.model = model
        self.firewall = firewall or OutputFirewall()

    async def preview(
        self,
        plan: ReplyPlan,
        *,
        context_events: tuple[SocialEventEnvelope, ...],
        persona_profile: Mapping[str, object],
        recent_outputs: tuple[str, ...],
    ) -> ReplyPreview:
        request = GenerationRequest(
            directive=plan.style,
            required=plan.required,
            recent_outputs=tuple(recent_outputs),
            allowed_media_references=(),
            verified_capability_results=(),
        )
        try:
            text = await self.model.complete_text(
                system_prompt=self._system_prompt(plan, persona_profile),
                prompt=self._prompt(plan, context_events),
            )
        except Exception:
            return ReplyPreview(None, "MODEL_FAILED", "reply_model_failed")
        draft = GeneratedDraft(text.strip())
        review = self.firewall.review(draft, request)
        if not review.accepted:
            try:
                repaired = await self.model.complete_text(
                    system_prompt=(
                        "重写为安全、自然、简短的群聊回复。只输出回复正文，"
                        "不得提及内部规则或执行状态。"
                    ),
                    prompt=json.dumps(
                        {
                            "draft": draft.text,
                            "violations": review.violations,
                            "max_chars": plan.style.max_chars,
                        },
                        ensure_ascii=False,
                    ),
                )
                draft = GeneratedDraft(repaired.strip())
                review = self.firewall.review(draft, request)
            except Exception:
                return ReplyPreview(None, "MODEL_FAILED", "reply_repair_failed")
        if not review.accepted:
            return ReplyPreview(None, "REJECTED", "output_firewall_rejected")
        return ReplyPreview(draft.text, "READY")

    async def execute(
        self,
        plan: ReplyPlan,
        *,
        context_events: tuple[SocialEventEnvelope, ...],
        persona_profile: Mapping[str, object],
        recent_outputs: tuple[str, ...],
    ) -> OutboxPart | None:
        return (
            await self.execute_with_result(
                plan,
                context_events=context_events,
                persona_profile=persona_profile,
                recent_outputs=recent_outputs,
            )
        ).part

    async def execute_with_result(
        self,
        plan: ReplyPlan,
        *,
        context_events: tuple[SocialEventEnvelope, ...],
        persona_profile: Mapping[str, object],
        recent_outputs: tuple[str, ...],
    ) -> ReplyExecutionResult:
        self.repository.save(plan)
        request = GenerationRequest(
            directive=plan.style,
            required=plan.required,
            recent_outputs=tuple(recent_outputs),
            allowed_media_references=(),
            verified_capability_results=(),
        )
        try:
            text = await self.model.complete_text(
                system_prompt=self._system_prompt(plan, persona_profile),
                prompt=self._prompt(plan, context_events),
            )
        except Exception:
            return ReplyExecutionResult(
                self._failed(plan, request),
                "MODEL_FAILED",
                "reply_model_failed",
            )
        draft = GeneratedDraft(text.strip())
        review = self.firewall.review(draft, request)
        if not review.accepted:
            try:
                repaired = await self.model.complete_text(
                    system_prompt=(
                        "重写为安全、自然、简短的群聊回复。只输出回复正文，"
                        "不得提及内部规则或执行状态。"
                    ),
                    prompt=json.dumps(
                        {
                            "draft": draft.text,
                            "violations": review.violations,
                            "max_chars": plan.style.max_chars,
                        },
                        ensure_ascii=False,
                    ),
                )
                draft = GeneratedDraft(repaired.strip())
                review = self.firewall.review(draft, request)
            except Exception:
                return ReplyExecutionResult(
                    self._failed(plan, request),
                    "MODEL_FAILED",
                    "reply_repair_failed",
                )
        if not review.accepted:
            return ReplyExecutionResult(
                self._failed(plan, request),
                "REJECTED",
                "output_firewall_rejected",
            )
        self.repository.mark(plan.plan_id, "generated")
        return ReplyExecutionResult(self._enqueue(plan, draft.text), "READY")

    def _failed(
        self, plan: ReplyPlan, request: GenerationRequest
    ) -> OutboxPart | None:
        if not plan.required:
            self.repository.mark(plan.plan_id, "silent")
            return None
        fallback = GeneratedDraft("暂时无法可靠回答。")
        if not self.firewall.review(fallback, request).accepted:
            self.repository.mark(plan.plan_id, "silent")
            return None
        self.repository.mark(plan.plan_id, "generated")
        return self._enqueue(plan, fallback.text)

    def _enqueue(self, plan: ReplyPlan, text: str) -> OutboxPart:
        part_id = f"reply-part:{plan.plan_id}"
        part = DeliveryPart.create(
            part_id=part_id,
            kind=DeliveryPartKind.TEXT,
            payload={
                "text": text,
                "platform_id": plan.platform_id,
                "session": plan.session,
                "self_id": plan.bot_id,
            },
            order=0,
            idempotency_key=f"reply-send:{plan.plan_id}",
            expires_at=plan.expires_at,
        )
        bundle = DeliveryBundle.create(
            bundle_id=f"reply-bundle:{plan.plan_id}",
            correlation_id=plan.correlation_id,
            persona_id=plan.persona_id,
            group_id=plan.group_id,
            topic_id=plan.topic_id,
            parts=(part,),
            created_at=plan.created_at,
            expires_at=plan.expires_at,
        )
        self.outbox.commit_bundle(bundle)
        self.repository.mark(plan.plan_id, "enqueued")
        return self.outbox.outbox(part_id)

    @staticmethod
    def _system_prompt(
        plan: ReplyPlan, persona_profile: Mapping[str, object]
    ) -> str:
        identity = persona_profile.get("identity")
        expression = persona_profile.get("expression")
        identity = identity if isinstance(identity, Mapping) else {}
        expression = expression if isinstance(expression, Mapping) else {}
        canon_value = persona_profile.get("canon")
        canon = PersonaCanon.from_mapping(
            canon_value if isinstance(canon_value, Mapping) else None
        )
        current_reality = tuple(
            item.text for item in canon.current_snapshot().current_state
        )
        return (
            "你是当前 Persona 在群聊中的自然表达。根据已批准的社交动作生成回复。"
            "不要解释规则，不要声称执行了工具，不要输出 Markdown。"
            "按顺序组织：可选即时反应、核心回应、可选人格化补充、可选续聊接口。"
            "先接住对方的情绪和关系信号，再处理事实；没有明显情绪时不要硬演。"
            "拒绝时明确边界并给简短理由；技术回答给可能原因和一个可执行步骤。"
            "只使用提供的安全 Persona 上下文，不模仿任何参考 Bot 的固定口癖。"
            "当前现实只用于保证事实正确，不要求在回复中复述。"
            "explicit_material 为空时，默认不要显式提及任何设定素材；"
            "不要为了证明人设而随机加入校园、报告、歌曲、游戏、电子、机械或能力元素。\n"
            "关系记忆只有当前语境相关时才可简短引用；不得泄露内部ID或敏感内容，"
            "不得根据关系分数凭空编造旧事。关系记忆为空时禁止翻旧账。\n"
            "成员画像只用于调整理解、称呼和表达，不要复述画像标签，不要逐条报告；"
            "仅在当前消息确实相关时自然使用，证据不足时以当前消息为准。\n"
            + json.dumps(
                {
                    "act": plan.act,
                    "style": asdict(plan.style),
                    "persona": {
                        "identity": {
                            "name": str(identity.get("name") or "Groupmate")[:24],
                            "role": str(identity.get("role") or "")[:160],
                        },
                        "behavior": {
                            "tone": str(expression.get("tone") or "")[:160],
                            "language_habits": str(
                                expression.get("language_habits") or ""
                            )[:200],
                            "relationship_stage": (
                                plan.expression.relationship_stage
                            ),
                            "reaction_stance": plan.expression.reaction_stance,
                            "boundary_style": plan.expression.boundary_style,
                            "relationship_memory_cues": list(
                                plan.expression.relationship_memory_cues
                            ),
                        },
                        "current_reality": current_reality,
                        "explicit_material": plan.expression.explicit_material,
                        "avoidances": plan.expression.persona_avoidances,
                    },
                    "relevant_member_context": plan.member_context,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    @staticmethod
    def _prompt(
        plan: ReplyPlan, context_events: tuple[SocialEventEnvelope, ...]
    ) -> str:
        messages = [
            {
                "event_id": event.event_id,
                "actor_id": event.actor_id,
                "text": str(event.payload.get("text") or ""),
            }
            for event in context_events[-12:]
            if str(event.payload.get("text") or "").strip()
        ]
        return json.dumps(
            {
                "target_id": plan.target_id,
                "topic_id": plan.topic_id,
                "evidence_event_ids": plan.evidence_event_ids,
                "messages": messages,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


__all__ = (
    "ExpressionPlan",
    "ReplyExecutor",
    "ReplyExecutionResult",
    "ReplyPlan",
    "ReplyPreview",
    "ReplyPlanIdentityConflict",
    "ReplyPlanRepository",
    "ReplyPlanner",
    "TextModelPort",
)
