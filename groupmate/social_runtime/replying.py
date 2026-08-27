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
from .actions.generation import (
    ExactChorusAllowance,
    GeneratedDraft,
    GenerationRequest,
    OutputFirewall,
)
from .actions.style import (
    PersonaStyleSnapshot,
    StyleContext,
    StyleDirective,
    StyleDirector,
)
from .contracts import SocialEventEnvelope
from .delivery.outbox import OutboxService
from .expression import ExpressionPlan, ExpressionPlanner
from .persona.modes import PersonaModeState
from .persistence.schema import connect_database, initialize_database
from .persona.canon import PersonaCanon
from .social_moves import SocialMove, SocialMovePlan
from .social_review import RealizedReply, SocialOutputReviewer
from .social_scenes import SocialScene, TargetScope
from .society.relationships import (
    PublicAffection,
    RelationshipProjection,
    RelationshipStage,
)
from .stances import Boundary, PermissionSnapshot, StanceDecision


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
    scene: SocialScene
    stance: StanceDecision
    move: SocialMovePlan
    relationship_projection_version: int
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
        scene = values.get("scene")
        stance = values.get("stance")
        move = values.get("move")
        if isinstance(scene, Mapping) and isinstance(stance, Mapping) and isinstance(move, Mapping):
            values["scene"] = SocialScene.create(**dict(scene))
            values["stance"] = StanceDecision.create(**dict(stance))
            values["move"] = SocialMovePlan.create(**dict(move))
        else:
            legacy_scene, legacy_stance, legacy_move = ReplyPlanRepository._legacy_decisions(values)
            values["scene"] = legacy_scene
            values["stance"] = legacy_stance
            values["move"] = legacy_move
        values.setdefault("relationship_projection_version", 0)
        return ReplyPlan(**values)

    @staticmethod
    def _legacy_decisions(
        values: Mapping[str, object],
    ) -> tuple[SocialScene, StanceDecision, SocialMovePlan]:
        evidence = tuple(values.get("evidence_event_ids", ())) or (
            str(values.get("correlation_id") or "legacy-event"),
        )
        target_id = str(values.get("target_id") or "").strip() or None
        scene = SocialScene.create(
            scene_kind="legacy_conservative",
            target_scope=(TargetScope.INDIVIDUAL if target_id else TargetScope.AMBIENT),
            target_id=target_id,
            literal_subject="旧版回复计划",
            user_move=str(values.get("act") or "legacy_reply"),
            continuity_event_ids=evidence,
            confidence=0.0,
        )
        stance = StanceDecision.create(
            attitude="NEUTRAL",
            willingness="WILLING",
            boundary="NONE",
            concession="NONE",
            effort="NORMAL",
            initiative="ALLOW",
            reason_event_ids=evidence,
            permission=PermissionSnapshot(True, "legacy_social_reply"),
        )
        move = SocialMovePlan.create(
            primary_move=SocialMove.DIRECT_ANSWER,
            mention_event_ids=evidence,
        )
        return scene, stance, move


class ReplyPlanner:
    def __init__(
        self,
        expression_planner: ExpressionPlanner | None = None,
        *,
        style_director: StyleDirector | None = None,
    ) -> None:
        self._expression_planner = expression_planner or ExpressionPlanner()
        self._style_director = style_director or StyleDirector()

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
        relationship_projection: RelationshipProjection | None = None,
        scene: SocialScene | None = None,
        stance: StanceDecision | None = None,
        move: SocialMovePlan | None = None,
        persona_mode: PersonaModeState | None = None,
        culture_patterns: tuple[str, ...] = (),
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
        resolved_public_affection = relationship or (
            PublicAffection.from_projection(relationship_projection)
            if relationship_projection is not None
            else PublicAffection(0.0, RelationshipStage.STRANGER)
        )
        expression = self._expression_planner.plan(
            lane=str(getattr(evaluation, "participation_lane", "AMBIENT")),
            act=selected.proposed_act,
            source_text=str(evaluation.source_event.payload.get("text") or ""),
            persona_profile=persona_profile,
            relationship=resolved_public_affection,
            recent_outputs=tuple(recent_outputs),
            relationship_memory_cues=tuple(relationship_memory_cues),
        )
        if scene is None or stance is None or move is None:
            scene, stance, move = self._legacy_decisions(
                evaluation, selected=selected, target_id=(selected.target_id or self._first(frame.candidate_audiences))
            )
        style = self._style_director.direct(
            StyleContext(
                persona=self._persona_style(persona_profile, evaluation.persona_id),
                mode=persona_mode or self._mode_for_stance(stance),
                relationship=relationship_projection,
                culture_patterns=tuple(culture_patterns),
                recent_outputs=tuple(recent_outputs),
                token_budget=40,
                scene=scene,
                stance=stance,
                move=move,
            )
        )
        return self._build_plan(
            evaluation=evaluation,
            frame=frame,
            selected=selected,
            intention_id=intention_id,
            expression=expression,
            style=style,
            scene=scene,
            stance=stance,
            move=move,
            relationship_projection_version=(
                relationship_projection.version if relationship_projection is not None else 0
            ),
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
        style: StyleDirective,
        scene: SocialScene,
        stance: StanceDecision,
        move: SocialMovePlan,
        relationship_projection_version: int,
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
            style=style,
            created_at=now,
            expires_at=min(int(selected.expires_at), now + 30),
            expression=expression,
            scene=scene,
            stance=stance,
            move=move,
            relationship_projection_version=int(relationship_projection_version),
            participation_lane=str(
                getattr(evaluation, "participation_lane", "AMBIENT")
                or "AMBIENT"
            ),
            member_context=str(member_context)[:1200],
        )

    @staticmethod
    def _legacy_decisions(
        evaluation: object, *, selected: object, target_id: str | None
    ) -> tuple[SocialScene, StanceDecision, SocialMovePlan]:
        evidence = tuple(selected.evidence_event_ids) or (evaluation.source_event.event_id,)
        scene = SocialScene.create(
            scene_kind="legacy_conservative",
            target_scope=TargetScope.INDIVIDUAL if target_id else TargetScope.AMBIENT,
            target_id=target_id,
            literal_subject=str(evaluation.source_event.payload.get("text") or "当前消息")[:160],
            user_move=str(selected.proposed_act),
            continuity_event_ids=evidence,
            confidence=0.0,
        )
        stance = StanceDecision.create(
            attitude="NEUTRAL",
            willingness="WILLING",
            boundary="NONE",
            concession="NONE",
            effort="NORMAL",
            initiative="ALLOW",
            reason_event_ids=evidence,
            permission=PermissionSnapshot(True, "legacy_social_reply"),
        )
        move = SocialMovePlan.create(
            primary_move=SocialMove.DIRECT_ANSWER,
            mention_event_ids=evidence,
        )
        return scene, stance, move

    @staticmethod
    def _persona_style(
        persona_profile: Mapping[str, object], persona_id: str
    ) -> PersonaStyleSnapshot:
        identity = persona_profile.get("identity")
        expression = persona_profile.get("expression")
        identity = identity if isinstance(identity, Mapping) else {}
        expression = expression if isinstance(expression, Mapping) else {}
        cues = tuple(
            value
            for value in (
                str(expression.get("tone") or "").strip(),
                str(expression.get("language_habits") or "").strip(),
            )
            if value
        )
        return PersonaStyleSnapshot(
            persona_id=str(persona_id),
            default_address=(str(identity.get("default_address") or "").strip() or None),
            expression=cues,
        )

    @staticmethod
    def _mode_for_stance(stance: StanceDecision) -> PersonaModeState:
        if stance.boundary in {Boundary.FIRM, Boundary.FINAL}:
            return PersonaModeState("boundary", (), stance.reason_event_ids, None)
        modifier = {
            "WARM": "warm",
            "AMUSED": "playful",
            "IRRITATED": "irritated",
        }.get(stance.attitude.value)
        return PersonaModeState(
            "social",
            () if modifier is None else (modifier,),
            stance.reason_event_ids,
            None,
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
        self.social_reviewer = SocialOutputReviewer()

    async def preview(
        self,
        plan: ReplyPlan,
        *,
        context_events: tuple[SocialEventEnvelope, ...],
        persona_profile: Mapping[str, object],
        recent_outputs: tuple[str, ...],
    ) -> ReplyPreview:
        if plan.move.primary_move is SocialMove.JOIN_CHORUS:
            realized = self._exact_chorus_reply(plan)
            if not self.social_reviewer.review(realized, plan).accepted:
                return ReplyPreview(None, "REJECTED", "social_review_rejected")
            request = self._generation_request(plan, recent_outputs)
            review = self.firewall.review(GeneratedDraft(realized.text), request)
            if not review.accepted:
                return ReplyPreview(None, "REJECTED", "output_firewall_rejected")
            return ReplyPreview(realized.text, "READY")
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
        if plan.move.primary_move is SocialMove.JOIN_CHORUS:
            realized = self._exact_chorus_reply(plan)
            if not self.social_reviewer.review(realized, plan).accepted:
                self.repository.mark(plan.plan_id, "silent")
                return ReplyExecutionResult(None, "REJECTED", "social_review_rejected")
            request = self._generation_request(plan, recent_outputs)
            if not self.firewall.review(GeneratedDraft(realized.text), request).accepted:
                self.repository.mark(plan.plan_id, "silent")
                return ReplyExecutionResult(None, "REJECTED", "output_firewall_rejected")
            self.repository.mark(plan.plan_id, "generated")
            return ReplyExecutionResult(
                self._enqueue(plan, realized.text), "READY"
            )
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

    @staticmethod
    def _exact_chorus_reply(plan: ReplyPlan) -> RealizedReply:
        return RealizedReply(
            text=str(plan.move.verbatim_payload or ""),
            covered_fact_ids=(),
            used_memory_ids=(),
            used_capability_ids=(),
            source_event_ids=plan.scene.chorus_event_ids,
        )

    @staticmethod
    def _generation_request(
        plan: ReplyPlan, recent_outputs: tuple[str, ...]
    ) -> GenerationRequest:
        allowance = ExactChorusAllowance(
            chain_id=str(plan.move.chorus_chain_id),
            payload=str(plan.move.verbatim_payload),
            source_event_ids=plan.scene.chorus_event_ids,
        )
        return GenerationRequest(
            directive=plan.style,
            required=plan.required,
            recent_outputs=tuple(recent_outputs),
            allowed_media_references=(),
            verified_capability_results=(),
            exact_chorus_allowance=allowance,
        )

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
            "直接完成指定动作；简单问题一句说完，需要证据时只问缺少的内容。"
            "不要复述问题，不要宣布自己正在回应，也不要在结尾追加通用服务邀请。"
            "拒绝时说清本轮边界；技术回答只使用消息中已有条件和已列事实。"
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
                    "social_decision": {
                        "scene_kind": plan.scene.scene_kind,
                        "literal_subject": plan.scene.literal_subject,
                        "user_move": plan.scene.user_move,
                        "attitude": plan.stance.attitude.value,
                        "willingness": plan.stance.willingness.value,
                        "boundary": plan.stance.boundary.value,
                        "primary_move": plan.move.primary_move.value,
                        "must_say": [asdict(fact) for fact in plan.move.must_say],
                        "may_say": [asdict(fact) for fact in plan.move.may_say],
                        "must_not_say": list(plan.move.must_not_say),
                        "ask_for": list(plan.move.ask_for),
                        "ending": plan.move.ending.value,
                    },
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
