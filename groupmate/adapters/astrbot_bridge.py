"""AstrBot composition boundary for Social Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from contextlib import suppress
from dataclasses import asdict, replace
import inspect
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from ..settings import SOCIAL_RUNTIME_DATABASE_NAME, SocialRuntimeSettings
from ..social_runtime.addressing import PersonaAddressResolver
from ..social_runtime.contracts import RuntimeMode, SocialEventEnvelope
from ..social_runtime.control.config_versions import (
    ConfigSnapshot,
    ConfigVersionRepository,
)
from ..social_runtime.control.message_traces import MessageTraceRepository
from ..social_runtime.cognition.ambient_worker import DirectAmbientWorker
from ..social_runtime.manager import SocialRuntimeManager
from ..social_runtime.knowledge.observation import KnowledgeObservationService
from ..social_runtime.knowledge.jobs import KnowledgeJobService
from ..social_runtime.knowledge.repository import (
    KnowledgeRepository,
    NegativeSnapshotKey,
)
from ..social_runtime.knowledge.resolver import KnowledgeEntityResolver
from ..social_runtime.knowledge.seeds import SeedImporter, load_bundled_seeds
from ..social_runtime.knowledge.sources import SafeSourceUrlPolicy
from ..social_runtime.ownership import ExternalTriggerPolicy
from ..social_runtime.persona.profile import GroupmatePersonaProfile
from ..social_runtime.persona.presets import PERSONA_CANON_PRESETS
from ..social_runtime.replying import ReplyExecutor, ReplyPlanner
from ..social_runtime.social_context import SceneContextBuilder
from ..social_runtime.social_moves import (
    RealizationMode,
    SocialMove,
    SocialMovePlanner,
)
from ..social_runtime.social_scenes import (
    ChorusTarget,
    SceneInterpretationResult,
    SocialScene,
    SocialSceneInterpreter,
    TargetScope,
)
from ..social_runtime.stances import PermissionSnapshot, StancePolicy
from ..social_runtime.delivery.dispatcher import DeliveryDispatcher
from ..social_runtime.actions.contracts import OutboxStatus
from ..social_runtime.actions.member_style import (
    IdentityImitationGuard,
    MemberStyleOverlayBuilder,
)
from .astrbot_delivery import AstrBotOneBotSender
from .astrbot_events import AstrBotEventTranslator
from .astrbot_models import AstrBotModelPort
from .astrbot_official_sources import (
    AstrBotOfficialSourceProbe,
    OfficialSourceHostCapability,
)
from .social_scene_model import SceneJsonModel
from .affection_card import AffectionCardPresenter
from .affection_query import AffectionQuery, is_affection_query
from .profile_query import (
    ProfileQueryResult,
    parse_profile_command,
)
from .deepseek_cognition import DeepSeekCognitionClient
from .deepseek_profile import DeepSeekProfileClient
from .deepseek_member_style import DeepSeekMemberStyleClient
from .imitation_commands import (
    ImitationCommandInterpreter,
    ImitationCommandResult,
    ImitationSessionController,
)
from .onebot_delivery import OneBotDeliveryAdapter
from ..social_runtime.profile.extractor import ProfileExtractor
from ..social_runtime.profile.repository import ProfileRepository
from ..social_runtime.profile.service import ProfileService
from ..social_runtime.profile.style_repository import MemberStyleRepository
from ..social_runtime.profile.style_service import MemberStyleService
from ..social_runtime.profile.contracts import ProfileFactCandidate
from ..social_runtime.profile.policy import ProfileEvidencePolicy
from ..social_runtime.society.affection_leaderboard import (
    AffectionLeaderboardService,
)


class AstrBotSocialRuntimeBridge:
    def __init__(
        self,
        context: object,
        settings: SocialRuntimeSettings,
        data_dir: Path,
        *,
        shadow_reviews: object | None = None,
        clock: Callable[[], float] | None = None,
        cognition_client_factory: Callable[[SocialRuntimeSettings], object]
        | None = None,
        profile_client_factory: Callable[[SocialRuntimeSettings], object]
        | None = None,
        member_style_client_factory: Callable[[SocialRuntimeSettings], object]
        | None = None,
        official_source_capability: OfficialSourceHostCapability | None = None,
    ) -> None:
        self.context = context
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.clock = time.time if clock is None else clock
        self._cognition_client_factory = (
            cognition_client_factory or self._new_cognition_client
        )
        self._profile_client_factory = (
            profile_client_factory or self._new_profile_client
        )
        self._member_style_client_factory = (
            member_style_client_factory or self._new_member_style_client
        )
        self._external_trigger_policy = ExternalTriggerPolicy.from_entries(
            command_prefixes=settings.external_command_prefixes,
            link_domains=settings.external_link_domains,
        )
        self.translator = AstrBotEventTranslator(
            settings.persona_id,
            external_trigger_policy=self._external_trigger_policy,
            clock=self.clock,
        )
        # Task scheduling remains deliberately outside this composition step.
        self.official_source_probe = AstrBotOfficialSourceProbe(
            official_source_capability,
            SafeSourceUrlPolicy(),
            timeout_seconds=5.0,
        )
        self._config_repository: ConfigVersionRepository | None = None
        self._manager: SocialRuntimeManager | None = None
        self._cognition_client: object | None = None
        self._profile_client: object | None = None
        self._profile_service: ProfileService | None = None
        self._knowledge_service: KnowledgeObservationService | None = None
        self._knowledge_job_service: KnowledgeJobService | None = None
        self._knowledge_repository: KnowledgeRepository | None = None
        self._member_style_repository: MemberStyleRepository | None = None
        self._member_style_service: MemberStyleService | None = None
        self._imitation_controller: ImitationSessionController | None = None
        self._trace_repository: MessageTraceRepository | None = None
        self.shadow_reviews = shadow_reviews
        self.shadow_review_error: str | None = None
        self.attention_wakeup_error: str | None = None
        self.cognition_diagnostics: list[str] = []
        self.reply_error: str | None = None
        self.member_style_overlay_error: str | None = None
        self.member_style_worker_error: str | None = None
        self.knowledge_error: str | None = None
        self.knowledge_search_adapter_unavailable = False
        self.trace_error: str | None = None
        self._reply_planner = ReplyPlanner()
        self._scene_context_builder = SceneContextBuilder()
        self._scene_interpreter: SocialSceneInterpreter | None = None
        self._stance_policy = StancePolicy()
        self._move_planner = SocialMovePlanner()
        self._reply_executor: ReplyExecutor | None = None
        self._reply_model: object | None = None
        self._dispatcher: DeliveryDispatcher | None = None
        self._reply_lock = asyncio.Lock()
        self._recent_outputs: dict[str, deque[str]] = {}
        self._attention_changed = asyncio.Event()
        self._attention_task: asyncio.Task[None] | None = None
        self._started = False

    async def prepare_affection_query(
        self, event: object
    ) -> AffectionQuery | None:
        """Build the local query result without entering chat or cognition."""

        if not self._started or self._manager is None:
            return None
        translated = self.translator.translate(event)
        if not is_affection_query(translated.payload.get("text")):
            return None
        group_id = str(translated.group_id or "").strip()
        if not group_id or self._manager.group_mode(group_id) is RuntimeMode.OFF:
            return None

        now = int(self.clock())
        participants = self.trace_repository.participants
        participants.remember(translated)
        bot_id = str(translated.payload.get("bot_id") or "").strip()
        since = now - 30 * 24 * 60 * 60
        recent_ids = participants.recent_actor_ids(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            since=since,
        )
        members = participants.active_members(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            since=since,
            exclude_actor_ids=(bot_id,),
        )
        group_name = str(
            translated.payload.get("group_name") or "当前群聊"
        )
        roster_complete = False
        group_getter = getattr(event, "get_group", None)
        if callable(group_getter):
            try:
                pending = group_getter(group_id)
                group = (
                    await asyncio.wait_for(pending, timeout=5.0)
                    if inspect.isawaitable(pending)
                    else pending
                )
                platform_members = self._normalize_group_members(
                    group,
                    bot_id=bot_id,
                    now=now,
                )
                if platform_members:
                    members = platform_members
                    roster_complete = True
                    platform_name = (
                        group.get("group_name")
                        if isinstance(group, Mapping)
                        else getattr(group, "group_name", "")
                    )
                    group_name = str(platform_name or group_name)
            except Exception:
                roster_complete = False
        leaderboard = AffectionLeaderboardService(self._manager.society).build(
            persona_id=self.settings.persona_id,
            group_id=group_id,
            group_name=group_name,
            requester_id=translated.actor_id,
            members=members,
            updated_at=now,
            recent_active_count=len(recent_ids - {bot_id}),
            roster_complete=roster_complete,
        )
        self._record_trace(
            self.trace_repository.record_affection_query,
            translated.event_id,
            now,
        )
        return AffectionQuery(
            leaderboard=leaderboard,
            pages=AffectionCardPresenter().pages(leaderboard),
        )

    async def prepare_imitation_transition(
        self, event: object
    ) -> ImitationCommandResult | None:
        """Commit an explicit group imitation request without entering chat AI.

        Recognition requires the platform's real bot-mention fact.  Ordinary
        messages return ``None`` so the normal command/profile/chat routes can
        continue unchanged.
        """

        if not self._started or self._manager is None:
            return None
        translated = self.translator.translate(event)
        group_id = str(translated.group_id or "").strip()
        if not group_id or self._manager.group_mode(group_id) is RuntimeMode.OFF:
            return None
        controller = self._imitation_controller
        if controller is None:
            return None
        self.trace_repository.participants.remember(translated)
        result = controller.handle(translated, now=int(self.clock()))
        if result is None or result.transition is None:
            return result
        identity = self._profile_snapshot(group_id)["identity"]
        transition = replace(
            result.transition,
            persona_name=str(identity.get("name") or self.settings.persona_name),
        )
        return replace(result, transition=transition)

    async def prepare_imitation_command(
        self, event: object
    ) -> ImitationCommandResult | None:
        """Render one complete response for a recognized state request.

        The transaction is committed before rendering.  A provider failure
        therefore changes only the confirmation wording, never the session.
        """

        result = await self.prepare_imitation_transition(event)
        if result is None:
            return None
        if result.transition is None:
            return replace(result, response_text=result.error_text)
        transition = result.transition
        session = transition.session
        persona_name = str(transition.persona_name or self.settings.persona_name)
        if transition.operation in {"STOPPED_BY_TARGET", "STOPPED_BY_ADMIN"}:
            return replace(
                result,
                response_text=f"好，不学了。我还是{persona_name}。",
            )

        style = self.member_style_repository.style(
            session.group_id,
            session.target_member_id,
            session.style_version,
        )
        expiry_text = self._imitation_expiry_text(session.expires_at)
        fallback = (
            f"好，我学{session.target_display_name}说话到{expiry_text}。"
            f"只是说话方式变了，我还是{persona_name}。"
        )
        if style is None or self._reply_model is None:
            return replace(result, response_text=fallback)
        overlay = MemberStyleOverlayBuilder().build(
            style,
            target_display_name=session.target_display_name,
            expires_at=session.expires_at,
        )
        try:
            text = await self._reply_model.complete_text(
                system_prompt=(
                    f"你是{persona_name}，正在给出一条群聊模仿确认。"
                    "确认本身就是第一次试演，只输出一条自然正文。"
                    f"参考这些定性表达特征：{list(overlay.directives)}。"
                    f"必须明说目标“{session.target_display_name}”、"
                    f"截止时间“{expiry_text}”、身份仍是“{persona_name}”。"
                    "只模仿说话方式，不借用目标的身份、经历、观点、关系或能力。"
                    "不要使用连接、频道、上线、系统指令、浓度或百分比包装。"
                ),
                prompt=(
                    f"开始模仿 {session.target_display_name}，"
                    f"到 {expiry_text}结束。"
                ),
            )
            violations = IdentityImitationGuard().review(
                text,
                overlay=overlay,
                required_facts=(
                    session.target_display_name,
                    expiry_text,
                    persona_name,
                ),
            )
            if violations:
                text = fallback
        except Exception:
            text = fallback
        return replace(result, response_text=text)

    @staticmethod
    def _imitation_expiry_text(expires_at: int) -> str:
        moment = datetime.fromtimestamp(
            int(expires_at), tz=ZoneInfo("Asia/Shanghai")
        )
        return (
            f"{moment.year}年{moment.month}月{moment.day}日"
            f"{moment.hour:02d}:{moment.minute:02d}"
        )

    async def prepare_profile_command(
        self, event: object
    ) -> ProfileQueryResult | None:
        """Handle one member's exact local profile command without a model."""

        if not self._started or self._manager is None:
            return None
        translated = self.translator.translate(event)
        command = parse_profile_command(translated.payload.get("text"))
        if command is None:
            return None
        group_id = str(translated.group_id or "").strip()
        subject_id = str(translated.actor_id or "").strip()
        if (
            not group_id
            or not subject_id
            or self._manager.group_mode(group_id) is RuntimeMode.OFF
        ):
            return None
        self.trace_repository.participants.remember(translated)
        repository = self._manager.profile_retriever.repository
        now = int(self.clock())
        facts = tuple(
            item
            for item in repository.facts(
                self.settings.persona_id, group_id, subject_id
            )
            if item.status in {"confirmed", "proposed"}
            and (item.valid_until is None or item.valid_until > now)
        )
        if command.kind == "show_self":
            return ProfileQueryResult(
                self._render_own_profile(
                    repository,
                    group_id=group_id,
                    subject_id=subject_id,
                    facts=facts,
                )
            )
        if command.kind in {"disable_personalization", "enable_personalization"}:
            enabled = command.kind == "enable_personalization"
            repository.set_personalization(
                self.settings.persona_id,
                group_id,
                subject_id,
                enabled=enabled,
                updated_at=now,
            )
            return ProfileQueryResult(
                "已恢复画像个性化。之后只会使用有证据且与你当前消息相关的画像。"
                if enabled
                else "已停止画像个性化。已有记录仍可查看和纠正，但不会用于判断或回复。"
            )
        number = int(command.fact_number or 0)
        if number < 1 or number > len(facts):
            return ProfileQueryResult("没有这个事实编号，请先发送“查看我的画像”。")
        selected = facts[number - 1]
        if command.kind == "delete_fact":
            repository.change_fact_status(
                selected.fact_id,
                persona_id=self.settings.persona_id,
                group_id=group_id,
                subject_id=subject_id,
                status="rejected",
                audit_id=f"profile-self-delete:{translated.event_id}",
                actor_id=subject_id,
                created_at=now,
            )
            self._refresh_snapshot_fact(
                repository,
                group_id=group_id,
                subject_id=subject_id,
                old_summary=selected.summary,
                new_summary=None,
                now=now,
            )
            return ProfileQueryResult(f"已删除第 {number} 条画像事实。")
        content = str(command.content or "").strip()
        digest = hashlib.sha256(
            f"{translated.event_id}:{selected.fact_id}:{content}".encode("utf-8")
        ).hexdigest()
        candidate = ProfileFactCandidate(
            candidate_id=f"profile-self-correction:{digest}",
            persona_id=self.settings.persona_id,
            group_id=group_id,
            subject_id=subject_id,
            category=selected.category,
            summary=content,
            source_kind="self_correction",
            source_actor_id=subject_id,
            source_event_ids=(translated.event_id,),
            confidence=1.0,
            evidence_count=1,
            observed_at=now,
        )
        correction = ProfileEvidencePolicy().correct(selected, candidate)
        repository.replace_fact(
            correction,
            audit_id=f"profile-self-correct:{translated.event_id}",
            actor_id=subject_id,
            created_at=now,
        )
        self._refresh_snapshot_fact(
            repository,
            group_id=group_id,
            subject_id=subject_id,
            old_summary=selected.summary,
            new_summary=content,
            now=now,
        )
        return ProfileQueryResult(f"已纠正第 {number} 条画像事实：{content}")

    def _refresh_snapshot_fact(
        self,
        repository: ProfileRepository,
        *,
        group_id: str,
        subject_id: str,
        old_summary: str,
        new_summary: str | None,
        now: int,
    ) -> None:
        snapshot = repository.snapshot(
            self.settings.persona_id, group_id, subject_id
        )
        if snapshot is None:
            return

        def updated(values: tuple[str, ...]) -> tuple[str, ...]:
            result = []
            for value in values:
                if value == old_summary:
                    if new_summary:
                        result.append(new_summary)
                else:
                    result.append(value)
            return tuple(dict.fromkeys(result))

        portrait = snapshot.one_line_portrait
        if portrait == old_summary:
            portrait = new_summary or "正在形成画像"
        repository.put_snapshot(
            replace(
                snapshot,
                one_line_portrait=portrait,
                group_roles=updated(snapshot.group_roles),
                individual_fingerprints=updated(
                    snapshot.individual_fingerprints
                ),
                preferences_and_boundaries=updated(
                    snapshot.preferences_and_boundaries
                ),
                source_revision=snapshot.source_revision + 1,
                generated_at=now,
            )
        )

    def _render_own_profile(
        self,
        repository: ProfileRepository,
        *,
        group_id: str,
        subject_id: str,
        facts: tuple,
    ) -> str:
        snapshot = repository.snapshot(
            self.settings.persona_id, group_id, subject_id
        )
        lines = ["我的画像"]
        if snapshot is None:
            lines.append("一句话：正在形成画像")
        else:
            lines.append(f"一句话：{snapshot.one_line_portrait}")
            if snapshot.group_roles:
                lines.append("群内角色：" + "、".join(snapshot.group_roles))
            if snapshot.preferences_and_boundaries:
                lines.append(
                    "偏好与边界："
                    + "；".join(snapshot.preferences_and_boundaries)
                )
        lines.append("我记得的事实：")
        if facts:
            lines.extend(
                f"[{index}] {fact.summary}"
                + ("（待确认）" if fact.status == "proposed" else "")
                for index, fact in enumerate(facts, start=1)
            )
        else:
            lines.append("暂无足够证据。")
        return "\n".join(lines)[:1800]

    @staticmethod
    def _normalize_group_members(
        group: object,
        *,
        bot_id: str,
        now: int,
    ) -> tuple[dict[str, object], ...]:
        value = (
            group.get("members")
            if isinstance(group, Mapping)
            else getattr(group, "members", None)
        )
        if not isinstance(value, (list, tuple)):
            return ()
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for member in value:
            if isinstance(member, Mapping):
                actor_id = str(
                    member.get("user_id") or member.get("actor_id") or ""
                ).strip()
                display_name = str(
                    member.get("card")
                    or member.get("nickname")
                    or member.get("display_name")
                    or "群成员"
                )
            else:
                actor_id = str(
                    getattr(member, "user_id", "")
                    or getattr(member, "actor_id", "")
                ).strip()
                display_name = str(
                    getattr(member, "card", "")
                    or getattr(member, "nickname", "")
                    or getattr(member, "display_name", "")
                    or "群成员"
                )
            if not actor_id or actor_id == bot_id or actor_id in seen:
                continue
            seen.add(actor_id)
            normalized.append(
                {
                    "actor_id": actor_id,
                    "display_name": " ".join(display_name.split())[:48]
                    or "群成员",
                    "updated_at": int(now),
                }
            )
        return tuple(normalized)

    @property
    def trace_repository(self) -> MessageTraceRepository:
        """Create trace storage only when the configured plugin actually needs it."""
        if self._trace_repository is None:
            self._trace_repository = MessageTraceRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
        return self._trace_repository

    @property
    def manager(self) -> SocialRuntimeManager:
        if self._manager is None:
            raise RuntimeError("Social Runtime is disabled")
        return self._manager

    @property
    def profile_service(self) -> ProfileService:
        if self._profile_service is None:
            raise RuntimeError("member profiling is disabled")
        return self._profile_service

    @property
    def knowledge_service(self) -> KnowledgeObservationService:
        if self._knowledge_service is None:
            raise RuntimeError("knowledge observation is unavailable")
        return self._knowledge_service

    @property
    def member_style_repository(self) -> MemberStyleRepository:
        repository = self._member_style_repository
        if repository is None:
            repository = MemberStyleRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
            self._member_style_repository = repository
        return repository

    @property
    def member_style_service(self) -> MemberStyleService:
        service = self._member_style_service
        if service is None:
            raise RuntimeError("member style distillation is disabled")
        return service

    def _member_style_overlay(self, group_id: str, *, now: int):
        """Resolve the session's frozen style version for this group only."""

        try:
            session = self.member_style_repository.active_session(
                str(group_id), now=int(now)
            )
            if session is None:
                self.member_style_overlay_error = None
                return None
            style = self.member_style_repository.style(
                session.group_id,
                session.target_member_id,
                session.style_version,
            )
            if style is None:
                self.member_style_overlay_error = "style_version_unavailable"
                return None
            overlay = MemberStyleOverlayBuilder().build(
                style,
                target_display_name=session.target_display_name,
                expires_at=session.expires_at,
            )
            self.member_style_overlay_error = None
            return overlay
        except Exception:
            # 风格是可选表达层，读取失败不得阻断闲聊主线。
            self.member_style_overlay_error = "style_overlay_unavailable"
            return None

    def runtime_status(self, group_id: str) -> dict[str, object]:
        """Return the bridge state that is effective for this group now."""

        manager = self._manager
        service = self._profile_service
        profile_health = getattr(service, "health", None)
        profile_status = (
            profile_health(str(group_id))
            if callable(profile_health)
            else {
                "enabled": bool(self.settings.profile_enabled),
                "task_running": False,
                "pending_count": 0,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_diagnostic": None,
            }
        )
        style_service = self._member_style_service
        active_session = (
            self.member_style_repository.active_session(
                str(group_id), now=int(self.clock())
            )
            if manager is not None
            else None
        )
        style_health = (
            style_service.health(str(group_id))
            if style_service is not None
            else {
                "enabled": False,
                "task_running": False,
                "last_diagnostic": None,
            }
        )
        member_style_status = {
            **style_health,
            "active_session": active_session is not None,
            "session_expires_at": (
                active_session.expires_at if active_session is not None else None
            ),
            "last_diagnostic": (
                self.member_style_overlay_error
                or self.member_style_worker_error
                or style_health.get("last_diagnostic")
            ),
        }
        knowledge_jobs = self._knowledge_job_service
        job_health = (
            knowledge_jobs.health()
            if knowledge_jobs is not None
            else {
                "accepting": False,
                "worker_running": False,
                "pending_or_retry": 0,
                "running": 0,
            }
        )
        knowledge_status = {
            **job_health,
            "last_diagnostic": self.knowledge_error,
            "search_adapter_unavailable": self.knowledge_search_adapter_unavailable,
        }
        if manager is None:
            blockers = (
                ["运行模式为 OFF"]
                if self.settings.runtime_mode == "OFF"
                else ["运行管理器尚未启动"]
            )
            return {
                "effective_runtime_mode": "OFF",
                "runtime_state": "STOPPED",
                "runtime_ready": False,
                "runtime_blockers": blockers,
                "profile_status": profile_status,
                "member_style_status": member_style_status,
                "knowledge_status": knowledge_status,
            }
        return {
            "effective_runtime_mode": manager.group_mode(str(group_id)).value,
            "runtime_state": "RUNNING",
            "runtime_ready": True,
            "runtime_blockers": [],
            "profile_status": profile_status,
            "member_style_status": member_style_status,
            "knowledge_status": knowledge_status,
        }

    def resolved_persona_status(self, group_id: str) -> dict[str, object]:
        """Expose identity labels without publishing private persona canon."""

        identity = self._profile_snapshot(str(group_id))["identity"]
        preset_labels = {
            "aemeath_current": "爱弥斯（当前剧情）",
            "custom": "兼容的自定义人格资料",
        }
        return {
            "name": str(identity["name"]),
            "aliases": [str(value) for value in identity.get("aliases", ())],
            "preset": self.settings.persona_preset,
            "preset_label": preset_labels.get(
                self.settings.persona_preset, "当前人格资料"
            ),
        }

    async def start(self) -> None:
        if self._started:
            return
        mode = RuntimeMode(self.settings.runtime_mode)
        if (
            mode in {RuntimeMode.SHADOW, RuntimeMode.SOCIAL_RUNTIME}
            and self.settings.enabled_groups
        ):
            config_repository = ConfigVersionRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
            self._config_repository = config_repository
            reply_model = AstrBotModelPort(
                self.context, self.settings.generation_provider
            )
            scene_interpreter = SocialSceneInterpreter(SceneJsonModel(reply_model))
            cognition_client = self._cognition_client_factory(self.settings)
            if cognition_client is None:
                raise RuntimeError("direct cognition client is unavailable")
            knowledge_service = None
            knowledge_job_service = None
            knowledge_repository = None
            knowledge_resolver = None
            if self.settings.knowledge_enabled:
                try:
                    knowledge_repository = KnowledgeRepository(
                        self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
                    )
                    SeedImporter(
                        knowledge_repository, clock=self.clock
                    ).import_all(load_bundled_seeds())
                    knowledge_service = KnowledgeObservationService(
                        repository=knowledge_repository,
                        group_ids=self.settings.enabled_groups,
                        install_salt=self._knowledge_install_salt(),
                        clock=self.clock,
                    )
                    knowledge_resolver = KnowledgeEntityResolver(
                        knowledge_repository
                    )
                    knowledge_job_service = KnowledgeJobService(
                        knowledge_repository,
                        probe=self.official_source_probe,
                        clock=self.clock,
                    )
                    self.knowledge_search_adapter_unavailable = (
                        not self.official_source_probe.available
                    )
                    self.knowledge_error = (
                        "knowledge_search_adapter_unavailable"
                        if self.knowledge_search_adapter_unavailable
                        else None
                    )
                except Exception:
                    knowledge_service = None
                    knowledge_repository = None
                    knowledge_resolver = None
                    knowledge_job_service = None
                    self.knowledge_search_adapter_unavailable = False
                    self.knowledge_error = "knowledge_seed_unavailable"
            else:
                self.knowledge_search_adapter_unavailable = False
                self.knowledge_error = None
            manager = SocialRuntimeManager(
                database_path=self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME,
                persona_id=self.settings.persona_id,
                mode=mode,
                enabled_groups=self.settings.enabled_groups,
                social_runtime_test_groups=self.settings.social_runtime_test_groups,
                cognition_workers={
                    "ambient_social_assessor": DirectAmbientWorker(
                        cognition_client
                    )
                },
                worker_concurrency_limit=self.settings.worker_concurrency_limit,
                worker_timeout_seconds=self.settings.cognition_timeout_seconds,
                persona_profile_loader=self._persona_config_snapshot,
                knowledge_resolver=knowledge_resolver,
                clock=self.clock,
            )
            profile_client = None
            profile_service = None
            member_style_service = None
            member_style_repository = self.member_style_repository
            imitation_controller = ImitationSessionController(
                ImitationCommandInterpreter(
                    admin_ids=self.settings.control_admin_ids,
                    participants=self.trace_repository.participants,
                ),
                member_style_repository,
            )
            if self.settings.profile_enabled:
                profile_client = self._profile_client_factory(self.settings)
                if profile_client is None:
                    raise RuntimeError("direct profile client is unavailable")
                try:
                    member_style_client = self._member_style_client_factory(
                        self.settings
                    )
                    if member_style_client is None:
                        raise RuntimeError(
                            "direct member style client is unavailable"
                        )
                    member_style_service = MemberStyleService(
                        repository=member_style_repository,
                        client=member_style_client,
                        interval_seconds=(
                            self.settings.profile_batch_interval_seconds
                        ),
                        clock=self.clock,
                    )
                    self.member_style_worker_error = None
                except Exception:
                    # 旧风格资产仍可供会话读取；只暂停新版本蒸馏。
                    member_style_service = None
                    self.member_style_worker_error = "style_worker_unavailable"
                profile_service = ProfileService(
                    repository=ProfileRepository(
                        self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
                    ),
                    extractor=ProfileExtractor(
                        profile_client, ProfileEvidencePolicy()
                    ),
                    persona_id=self.settings.persona_id,
                    group_ids=self.settings.enabled_groups,
                    batch_size=self.settings.profile_batch_messages,
                    interval_seconds=(
                        self.settings.profile_batch_interval_seconds
                    ),
                    clock=self.clock,
                    style_service=member_style_service,
                )
            try:
                await manager.start()
                if profile_service is not None:
                    await profile_service.start()
                if knowledge_service is not None:
                    await knowledge_service.start()
                if knowledge_job_service is not None:
                    await knowledge_job_service.start()
                self._manager = manager
                self._cognition_client = cognition_client
                self._profile_client = profile_client
                self._profile_service = profile_service
                self._knowledge_service = knowledge_service
                self._knowledge_job_service = knowledge_job_service
                self._knowledge_repository = knowledge_repository
                self._member_style_service = member_style_service
                self._imitation_controller = imitation_controller
                self._scene_interpreter = scene_interpreter
                self._reply_executor = ReplyExecutor(
                    manager.reply_plans,
                    manager.outbox,
                    reply_model,
                )
                self._reply_model = reply_model
                self._dispatcher = DeliveryDispatcher(
                    manager.outbox,
                    OneBotDeliveryAdapter(
                        AstrBotOneBotSender(self.context), clock=self.clock
                    ),
                    receipt_handler=manager.coordinator.apply_delivery_receipt,
                )
                self._attention_task = asyncio.create_task(
                    self._attention_wakeup_loop(manager),
                    name=f"groupmate-attention:{self.settings.persona_id}",
                )
            except BaseException:
                self._manager = None
                self._cognition_client = None
                self._profile_client = None
                self._profile_service = None
                self._knowledge_service = None
                self._knowledge_job_service = None
                self._knowledge_repository = None
                self._member_style_service = None
                self._imitation_controller = None
                self._scene_interpreter = None
                self._reply_model = None
                await self._close_resources(
                    knowledge_job_service,
                    self.official_source_probe,
                    knowledge_service,
                    profile_service,
                    manager,
                    cognition_client,
                    profile_client,
                    suppress_errors=True,
                )
                raise
        self._started = True
        self._reconcile_shadow_reviews()

    async def handle_event(self, event: object):
        if not self._started:
            await self.start()
        if self._manager is None:
            return None
        translated = self._resolve_interaction(self.translator.translate(event))
        if self._owns_host_response(translated):
            stop_event = getattr(event, "stop_event", None)
            if callable(stop_event):
                stop_event()
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )
        await self._observe_profile(translated)
        if translated.payload.get("social_eligible") is not False:
            self._record_trace(
                self.trace_repository.mark_entered,
                translated.event_id,
                int(self.clock()),
            )
        result = await self._manager.ingest(translated)
        if result is not None and result.inserted:
            await self._observe_knowledge(translated)
            evaluations = await self._manager.drain()
            await self._handle_evaluations(evaluations)
            self._attention_changed.set()
        self._reconcile_shadow_reviews()
        return result

    def _owns_host_response(self, event: SocialEventEnvelope) -> bool:
        if self._manager is None:
            return False
        payload = event.payload
        return bool(
            self._manager.group_mode(str(event.group_id or ""))
            is RuntimeMode.SOCIAL_RUNTIME
            and payload.get("direct_address")
            and payload.get("social_eligible") is not False
            and payload.get("interaction_owner") != "EXTERNAL_PLUGIN"
        )

    async def observe_event(self, event: object) -> None:
        """Record arrival and route facts without entering Social Runtime."""

        if not self.settings.enabled_groups:
            return

        translated = self._resolve_interaction(self.translator.translate(event))
        self._record_trace(
            self.trace_repository.record_received,
            translated,
            self.settings.runtime_mode,
            int(self.clock()),
        )
        await self._observe_profile(translated)

    async def _observe_profile(self, event: SocialEventEnvelope) -> None:
        service = self._profile_service
        if service is None:
            return
        payload = event.payload
        # Commands belong to their command owner. They are operational input,
        # not evidence about the member's personality or preferences.
        if (
            payload.get("social_eligible") is False
            or str(payload.get("interaction_owner") or "").upper()
            == "EXTERNAL_PLUGIN"
            or parse_profile_command(payload.get("text")) is not None
        ):
            return
        await service.observe(event)

    async def _observe_knowledge(self, event: SocialEventEnvelope) -> None:
        service = self._knowledge_service
        if service is None:
            return
        try:
            await service.observe(event)
            self.knowledge_error = (
                "knowledge_search_adapter_unavailable"
                if self.knowledge_search_adapter_unavailable
                else None
            )
        except Exception:
            # Knowledge learning is optional and must never break chat ingest.
            self.knowledge_error = "knowledge_observation_failed"

    def _knowledge_install_salt(self) -> str:
        """Return a stable per-install salt without putting it in SQLite."""

        path = self.data_dir / ".groupmate-knowledge-salt"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            generated = secrets.token_hex(32)
            try:
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(generated)
                with suppress(OSError):
                    path.chmod(0o600)
                value = generated
            except FileExistsError:
                value = path.read_text(encoding="utf-8").strip()
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise RuntimeError("knowledge install salt is invalid")
        return value

    def _resolve_interaction(
        self, event: SocialEventEnvelope
    ) -> SocialEventEnvelope:
        profile = self._profile_snapshot(str(event.group_id or ""))
        identity = profile["identity"]
        resolver = PersonaAddressResolver(
            str(identity["name"]),
            tuple(str(value) for value in identity.get("aliases", ())),
        )
        resolution = resolver.resolve(
            text=str(event.payload.get("text") or ""),
            mentions_bot=bool(event.payload.get("mentions_bot")),
            reply_to_bot=bool(event.payload.get("reply_to_bot")),
        )
        ownership = self._external_trigger_policy.classify(
            resolution.address_remainder
        )
        payload = dict(event.payload)
        payload.update(asdict(resolution))
        payload["direct_address"] = resolution.addressed_to_bot
        if ownership is not None:
            ownership_source = ownership.source
            if resolution.matched_alias is not None:
                ownership_source = f"alias_stripped_{ownership_source}"
            payload.update(
                {
                    "interaction_owner": ownership.owner.value,
                    "social_eligible": ownership.social_eligible,
                    "owner_ref": ownership.owner_ref,
                    "ownership_source": ownership_source,
                    "external_trigger_kind": ownership.trigger_kind,
                    "external_trigger_value": ownership.trigger_value,
                }
            )
        values = event.to_dict()
        values["payload"] = payload
        return SocialEventEnvelope.create(**values)

    def _profile_snapshot(self, group_id: str) -> dict[str, object]:
        snapshot = self._persona_config_snapshot(group_id)
        configured = snapshot.config["persona_profile"]
        return GroupmatePersonaProfile.from_mapping(configured).to_mapping()

    def _persona_config_snapshot(self, group_id: str) -> ConfigSnapshot:
        repository = self._config_repository
        if repository is None:
            repository = ConfigVersionRepository(
                self.data_dir / SOCIAL_RUNTIME_DATABASE_NAME
            )
            self._config_repository = repository
        snapshot = repository.snapshot(
            persona_id=self.settings.persona_id,
            group_id=group_id or None,
        )
        configured = snapshot.config.get("persona_profile")
        if isinstance(configured, Mapping):
            values = dict(configured)
            if "canon" not in values:
                values["canon"] = PERSONA_CANON_PRESETS[
                    self.settings.persona_preset
                ].to_mapping()
            profile = GroupmatePersonaProfile.from_mapping(values).to_mapping()
        else:
            profile = GroupmatePersonaProfile.default().to_mapping()
            profile["identity"]["name"] = self.settings.persona_name
            profile["identity"]["aliases"] = list(self.settings.persona_aliases)
            profile["canon"] = PERSONA_CANON_PRESETS[
                self.settings.persona_preset
            ].to_mapping()
            profile = GroupmatePersonaProfile.from_mapping(profile).to_mapping()
        config = dict(snapshot.config)
        config["persona_profile"] = profile
        return ConfigSnapshot(snapshot.version, config)

    async def _attention_wakeup_loop(
        self, manager: SocialRuntimeManager
    ) -> None:
        while self._manager is manager:
            self._attention_changed.clear()
            try:
                deadline = await manager.next_attention_deadline()
                if deadline is None:
                    await self._attention_changed.wait()
                    continue
                delay = max(0.0, float(deadline) - float(self.clock()))
                try:
                    await asyncio.wait_for(
                        self._attention_changed.wait(), timeout=delay
                    )
                except TimeoutError:
                    evaluations = await manager.drain(now=deadline)
                    await self._handle_evaluations(evaluations)
                    self._reconcile_shadow_reviews()
                self.attention_wakeup_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.attention_wakeup_error = f"{type(exc).__name__}: {exc}"
                await self._attention_changed.wait()

    async def _handle_evaluations(self, evaluations: tuple[object, ...]) -> None:
        if self._manager is None:
            return
        async with self._reply_lock:
            handled_groups: set[str] = set()
            ordered = sorted(
                evaluations,
                key=lambda item: (
                    0
                    if getattr(getattr(item, "frame", None), "trigger_kind", "")
                    == "FAST"
                    else 1
                ),
            )
            for evaluation in ordered:
                evaluation = self._attach_shadow_knowledge_diagnostic(
                    evaluation, now=int(self.clock())
                )
                group_id = str(
                    getattr(getattr(evaluation, "source_event", None), "group_id", "")
                    or ""
                )
                if not group_id or group_id in handled_groups:
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                source_event = getattr(evaluation, "source_event", None)
                if (
                    source_event is None
                    or source_event.payload.get("social_eligible") is False
                ):
                    # 外部命令已完成所有权判定，只记交接结果，不进入场景或回复模型。
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                persona_profile = self._manager.persona_profile_mapping(
                    group_id,
                    int(getattr(evaluation, "config_version", 0)),
                )
                frame = getattr(evaluation, "frame", None)
                subject_id = str(getattr(source_event, "actor_id", "") or "") or str(
                    next(
                        iter(getattr(frame, "candidate_audiences", ()) or ()),
                        "",
                    )
                )
                if self._scene_interpreter is None:
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                relationship_projection = self._manager.relationship_projection(
                    group_id, subject_id
                )
                relationship = self._manager.relationship_affection(group_id, subject_id)
                try:
                    relationship_memories = self._safe_relationship_memories(
                        self._manager.relationship_memory_records(
                            group_id, subject_id
                        )
                    )
                    relationship_memory_cues = (
                        self._manager.relationship_memory_cues(
                            group_id,
                            subject_id,
                            text=str(source_event.payload.get("text") or "")
                            if source_event is not None
                            else "",
                            now=int(self.clock()),
                        )
                        if subject_id
                        else ()
                    )
                except Exception:
                    # Relationship memory enriches expression but must never
                    # block an already approved social reply.
                    relationship_memory_cues = ()
                    relationship_memories = ()
                recent_outputs = tuple(
                    self._recent_outputs.get(group_id, ())
                )
                profile_retrieval = self._manager.member_profile_retrieval(
                    source_event, max_chars=1200
                )
                topic_id = next(
                    iter(getattr(frame, "focus_topic_ids", ()) or ()), None
                )
                identity = persona_profile.get("identity")
                identity = identity if isinstance(identity, Mapping) else {}
                scene_context = self._scene_context_builder.build(
                    source_event=source_event,
                    context_events=tuple(
                        getattr(evaluation, "context_events", ()) or ()
                    ),
                    focus_event_ids=tuple(
                        getattr(frame, "focus_event_ids", ()) or ()
                    ),
                    target_id=subject_id or None,
                    topic_id=topic_id,
                    persona_actor_id=self._manager.persona_id,
                    persona_aliases=(
                        str(identity.get("name") or "爱弥斯"),
                        *tuple(identity.get("aliases", ()) or ()),
                    ),
                    member_refs=self._manager.group_member_refs(group_id),
                    profile=profile_retrieval,
                    relationship_memories=relationship_memories,
                    topic_understanding=getattr(
                        evaluation, "topic_understanding", None
                    ),
                ).with_chorus(getattr(evaluation, "chorus_evidence", None))
                if (
                    source_event.event_type == "temporal.opportunity_due"
                    and not str(
                        source_event.payload.get("literal_subject") or ""
                    ).strip()
                ):
                    interpretation = SceneInterpretationResult(
                        SocialScene.create(
                            scene_kind="proactive_no_entry",
                            target_scope=TargetScope.INDIVIDUAL,
                            target_id=subject_id,
                            literal_subject="没有具体切入点的主动机会",
                            user_move="autonomous_opportunity_without_subject",
                            continuity_event_ids=tuple(
                                getattr(frame, "focus_event_ids", ()) or (
                                    source_event.event_id,
                                )
                            ),
                            confidence=1.0,
                        ),
                        "proactive_subject_missing",
                    )
                else:
                    interpretation = await self._scene_interpreter.interpret(
                        scene_context
                    )
                subject_relationship = None
                if (
                    interpretation.scene.chorus_target
                    is ChorusTarget.MEMBER
                    and interpretation.scene.chorus_target_id
                ):
                    subject_relationship = self._manager.relationship_projection(
                        group_id, interpretation.scene.chorus_target_id
                    )
                persona_snapshot = await self._manager.persona_snapshot(
                    group_id, int(getattr(evaluation, "config_version", 0))
                )
                culture_patterns = (
                    ("light_member_banter",)
                    if getattr(evaluation, "chorus_evidence", None) is not None
                    else ()
                )
                stance = self._stance_policy.decide(
                    interpretation.scene,
                    actor_relationship=relationship_projection,
                    subject_relationship=subject_relationship,
                    culture_patterns=culture_patterns,
                    permission=PermissionSnapshot(True, "social_reply_governed"),
                    mode_modifiers=persona_snapshot.modifiers,
                    memory_event_ids=tuple(
                        memory.relationship_event_id
                        for memory in relationship_memories
                    ),
                )
                move = self._move_planner.plan(
                    interpretation.scene,
                    stance,
                    profile=profile_retrieval,
                    memories=relationship_memories,
                )
                scene_summary, stance_summary, move_summary = (
                    self._safe_social_decision_summaries(
                        interpretation.scene, stance, move
                    )
                )
                evaluation = replace(
                    evaluation,
                    social_scene_summary=scene_summary,
                    social_stance_summary=stance_summary,
                    social_move_summary=move_summary,
                    social_would_reply=move.primary_move is not SocialMove.SILENCE,
                )
                if move.primary_move is SocialMove.SILENCE:
                    diagnostic = (
                        interpretation.diagnostic_code or "social_move_silence"
                    )
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        replace(
                            evaluation,
                            reply_diagnostic=diagnostic,
                        ),
                        int(self.clock()),
                    )
                    self._record_trace(
                        self.trace_repository.record_social_decision,
                        source_event.event_id,
                        scene=interpretation.scene,
                        stance=stance,
                        move=move,
                        diagnostic_code=diagnostic,
                        now=int(self.clock()),
                    )
                    if self._manager.group_mode(group_id) is RuntimeMode.SHADOW:
                        self._manager.update_shadow_review_evidence(evaluation)
                    continue
                plan = self._reply_planner.plan(
                    evaluation,
                    now=int(self.clock()),
                    persona_profile=persona_profile,
                    relationship=relationship,
                    relationship_projection=relationship_projection,
                    recent_outputs=recent_outputs,
                    relationship_memory_cues=relationship_memory_cues,
                    member_context=profile_retrieval.prompt_text,
                    scene=interpretation.scene,
                    stance=stance,
                    move=move,
                    culture_patterns=culture_patterns,
                    member_style_overlay=(
                        self._member_style_overlay(
                            group_id, now=int(self.clock())
                        )
                        if move.realization_mode is RealizationMode.GENERATED
                        else None
                    ),
                )
                if plan is None:
                    self._record_trace(
                        self.trace_repository.record_evaluation,
                        evaluation,
                        int(self.clock()),
                    )
                    continue
                mode = self._manager.group_mode(group_id)
                if mode is RuntimeMode.SHADOW and self._reply_executor is not None:
                    preview = await self._reply_executor.preview(
                        plan,
                        context_events=tuple(
                            getattr(evaluation, "context_events", ())
                        ),
                        persona_profile=persona_profile,
                        recent_outputs=(),
                    )
                    evaluation = replace(
                        evaluation,
                        candidate_response=preview.text,
                        reply_diagnostic=preview.diagnostic_code,
                    )
                    self._manager.update_shadow_review_evidence(evaluation)
                    if preview.status == "READY" and preview.text:
                        self._remember_output(group_id, preview.text)
                self._record_trace(
                    self.trace_repository.record_evaluation,
                    evaluation,
                    int(self.clock()),
                )
                if source_event is not None:
                    self._record_trace(
                        self.trace_repository.record_plan,
                        str(getattr(source_event, "event_id", "")),
                        plan,
                        int(self.clock()),
                    )
                handled_groups.add(group_id)
                try:
                    if mode is RuntimeMode.SHADOW:
                        self._manager.reply_plans.save(plan)
                        continue
                    if self._reply_executor is None:
                        raise RuntimeError("reply executor is unavailable")
                    execution = await self._reply_executor.execute_with_result(
                        plan,
                        context_events=tuple(
                            getattr(evaluation, "context_events", ())
                        ),
                        persona_profile=persona_profile,
                        recent_outputs=(),
                    )
                    if execution.usable_for_lease:
                        text = str(
                            execution.part.part.payload.get("text") or ""
                        ).strip()
                        if text:
                            self._remember_output(group_id, text)
                        await self._manager.record_usable_reply(
                            plan, now=int(self.clock())
                        )
                    await self._dispatch_ready()
                    self.reply_error = None
                except Exception as exc:
                    self.reply_error = f"{type(exc).__name__}: {exc}"

    def _attach_shadow_knowledge_diagnostic(
        self, evaluation: object, *, now: int
    ) -> object:
        """Attach a bounded, SHADOW-only view of locally persisted evidence."""

        if getattr(evaluation, "runtime_mode", None) is not RuntimeMode.SHADOW:
            return evaluation
        frame = getattr(evaluation, "topic_understanding", None)
        reference = getattr(frame, "version_reference", None)
        repository = self._knowledge_repository
        if repository is None or reference is None:
            return evaluation
        try:
            diagnostic = self._shadow_knowledge_diagnostic(
                repository, frame, now=int(now)
            )
        except Exception:
            diagnostic = {
                "status": "official_unavailable",
                "probe_status": "unavailable",
                "probe_reason": "knowledge_diagnostic_unavailable",
                "source_domains": [],
                "evidence_level": None,
                "checked_at": None,
                "fresh_until": None,
                "release_revision": 0,
                "tracks": {
                    "release": None,
                    "official": None,
                    "rumor": None,
                },
            }
        try:
            return replace(evaluation, knowledge_diagnostic=diagnostic)
        except TypeError:
            return evaluation

    @staticmethod
    def _shadow_knowledge_diagnostic(
        repository: KnowledgeRepository, frame: object, *, now: int
    ) -> dict[str, object]:
        reference = getattr(frame, "version_reference", None)
        game_id = str(getattr(reference, "game_id", "") or "")
        region = str(getattr(reference, "region", "") or "global")
        platform = str(getattr(reference, "platform", "") or "all")
        slots = repository.load_release_state(game_id, region, platform)
        desired_release_state = {
            "current": "current",
            "new": "current",
            "recent_update": "current",
            "next": "future",
            "previous": "past",
        }.get(str(getattr(reference, "relative_kind", "") or ""))
        matching_slots = tuple(
            item
            for item in slots
            if desired_release_state is not None
            and item.release_state == desired_release_state
        )
        slot = (
            matching_slots[0]
            if len(matching_slots) == 1
            else slots[0]
            if not matching_slots and len(slots) == 1
            else None
        )
        revision = max((item.revision for item in slots), default=0)
        tracks = {
            "release": None if slot is None else slot.release_state,
            "official": None if slot is None else slot.official_state,
            "rumor": None if slot is None else slot.rumor_state,
        }

        jobs = tuple(
            item
            for item in repository.knowledge_jobs()
            if item.entity_id == game_id
            and item.job_kind
            in {"official_daily_probe", "time_boundary_revalidation"}
            and item.request.get("region") == region
            and item.request.get("platform") == platform
        )
        latest_job = max(
            jobs,
            key=lambda item: (item.updated_at, item.created_at, item.job_id),
            default=None,
        )
        seed = next(
            (
                item
                for item in load_bundled_seeds()
                if item.game.entity_id == game_id
            ),
            None,
        )
        registered_domains = (
            {item.source_id: item.domain for item in seed.official_sources}
            if seed is not None
            else {}
        )
        source_domains = []
        if latest_job is not None:
            for source in tuple(latest_job.request.get("sources") or ()):
                if not isinstance(source, Mapping):
                    continue
                domain = registered_domains.get(str(source.get("source_id") or ""))
                if domain and domain not in source_domains:
                    source_domains.append(domain)
        if not source_domains:
            if seed is not None:
                source_domains = list(
                    dict.fromkeys(item.domain for item in seed.official_sources)
                )

        disclosure = str(
            getattr(reference, "disclosure_kind", "") or ""
        )
        ambiguity_codes = tuple(getattr(frame, "ambiguity_codes", ()) or ())
        rumor_query = disclosure == "rumor" or "risk:rumor_status" in ambiguity_codes
        evidence_level = (
            "official"
            if slot is not None and slot.official_state != "none"
            else "unofficial"
            if slot is not None and slot.rumor_state != "none_observed"
            else None
        )
        official_checked_at = (
            None if slot is None else slot.official_checked_at
        )
        checked_at = official_checked_at
        fresh_until = None if slot is None else slot.fresh_until

        disputed = bool(
            slot is not None
            and (slot.status == "disputed" or slot.rumor_state == "conflicted")
        )
        boundary = bool(
            slot is not None
            and slot.release_at is not None
            and int(now) >= slot.release_at
            and slot.official_state != "released"
        )
        stale = slot is not None and slot.fresh_until <= int(now)

        def safe_probe_failure(value: object) -> tuple[str, str]:
            reason = str(value or "")
            statuses = {
                "official_probe_partial": "partial",
                "official_probe_timed_out": "timed_out",
                "official_probe_unavailable": "unavailable",
                "official_probe_failed": "failed",
                "official_probe_cancelled": "failed",
                "knowledge_job_recovered": "failed",
            }
            if reason not in statuses:
                reason = "official_probe_failed"
            return statuses[reason], reason

        if disputed:
            status = "evidence_disputed"
            probe_status = "complete"
            probe_reason = "evidence_disputed"
        elif boundary:
            status = "knowledge_stale"
            probe_status = "complete"
            probe_reason = "release_boundary_revalidation_required"
        elif rumor_query:
            probe_status = "not_requested"
            probe_reason = "rumor_requires_explicit_search"
            if slot is not None and slot.rumor_state not in {
                "none_observed",
                "stale",
            }:
                status = "rumor_observed"
                checked_at = slot.rumor_checked_at
            elif stale or (slot is not None and slot.rumor_state == "stale"):
                status = "knowledge_stale"
            else:
                status = "rumor_not_probed"
        else:
            snapshot = repository.valid_negative_snapshot(
                NegativeSnapshotKey(
                    game_entity_id=game_id,
                    query_intent="verify_version_state",
                    region=region,
                    platform=platform,
                ),
                int(now),
            )
            if snapshot is not None:
                status = "negative_snapshot_valid"
                probe_status = "complete"
                probe_reason = snapshot.diagnostic_code
                checked_at = snapshot.checked_at
                fresh_until = snapshot.expires_at
                revision = snapshot.version_state_revision
            elif stale:
                status = "knowledge_stale"
                if latest_job is not None and latest_job.diagnostic_code:
                    probe_status, probe_reason = safe_probe_failure(
                        latest_job.diagnostic_code
                    )
                else:
                    probe_status = (
                        "complete"
                        if latest_job is not None
                        and latest_job.status == "completed"
                        else "not_requested"
                    )
                    probe_reason = "knowledge_stale"
            elif latest_job is not None and latest_job.status == "completed":
                status = "official_complete"
                probe_status = "complete"
                probe_reason = (
                    "official_evidence_fresh"
                    if slot is not None
                    else "official_probe_complete_no_claim"
                )
                checked_at = max(
                    value
                    for value in (official_checked_at, latest_job.updated_at)
                    if value is not None
                )
            elif latest_job is not None and latest_job.diagnostic_code:
                probe_status, probe_reason = safe_probe_failure(
                    latest_job.diagnostic_code
                )
                status = {
                    "partial": "official_partial",
                    "timed_out": "official_timed_out",
                    "unavailable": "official_unavailable",
                    "failed": "official_failed",
                }[probe_status]
            elif slot is not None:
                status = "official_complete"
                probe_status = "not_recorded"
                probe_reason = "official_evidence_fresh"
            else:
                status = "official_unverified"
                probe_status = "not_requested"
                probe_reason = "official_probe_not_recorded"

        return {
            "status": status,
            "probe_status": probe_status,
            "probe_reason": probe_reason,
            "source_domains": source_domains[:8],
            "evidence_level": evidence_level,
            "checked_at": checked_at,
            "fresh_until": fresh_until,
            "release_revision": revision,
            "tracks": tracks,
        }

    def _remember_output(self, group_id: str, text: str) -> None:
        history = self._recent_outputs.setdefault(str(group_id), deque(maxlen=8))
        history.append(str(text).strip())

    @staticmethod
    def _safe_relationship_memories(records: tuple[object, ...]) -> tuple[object, ...]:
        """Keep only current, normal-sensitivity memories in model-visible context."""

        return tuple(
            record
            for record in records
            if getattr(record, "resolved_at", None) is None
            and str(getattr(record, "sensitivity", "")) == "normal"
            and float(getattr(record, "confidence", 0.0)) >= 0.82
        )[:8]

    @staticmethod
    def _safe_social_decision_summaries(
        scene: object, stance: object, move: object
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        def enum_value(value: object) -> str:
            return str(getattr(value, "value", value) or "")

        scene_summary = {
            "scene_kind": str(getattr(scene, "scene_kind", ""))[:48],
            "target_scope": enum_value(getattr(scene, "target_scope", "")),
            "chorus_target": enum_value(
                getattr(scene, "chorus_target", "NONE")
            ),
            "chorus_chain_id": (
                str(getattr(scene, "chorus_chain_id", "") or "")[:80] or None
            ),
            "chorus_participant_count": min(
                99,
                len(tuple(getattr(scene, "chorus_participant_ids", ()) or ())),
            ),
        }
        stance_summary = {
            field: enum_value(getattr(stance, field, ""))
            for field in ("attitude", "willingness", "boundary", "effort")
        }
        facts = (
            *tuple(getattr(move, "must_say", ()) or ()),
            *tuple(getattr(move, "may_say", ()) or ()),
        )
        move_summary = {
            "primary_move": enum_value(getattr(move, "primary_move", "")),
            "ending": enum_value(getattr(move, "ending", "")),
            "realization_mode": enum_value(
                getattr(move, "realization_mode", "")
            ),
            "fact_categories": list(
                dict.fromkeys(
                    str(getattr(fact, "category", ""))[:40]
                    for fact in facts
                    if str(getattr(fact, "category", "")).strip()
                )
            ),
        }
        return scene_summary, stance_summary, move_summary

    async def _dispatch_ready(self) -> None:
        if self._manager is None or self._dispatcher is None:
            return
        while True:
            part = await self._dispatcher.dispatch_next(now=int(self.clock()))
            if part is None:
                return
            try:
                plan = self._manager.reply_plans.by_correlation(
                    part.correlation_id
                )
                status = "sent" if part.status is OutboxStatus.SENT else part.status.value
                self._manager.reply_plans.mark(plan.plan_id, status)
                if (
                    part.status is OutboxStatus.SENT
                    and plan.move.primary_move is SocialMove.JOIN_CHORUS
                    and plan.move.chorus_chain_id
                ):
                    # 只有平台给出成功回执后才记为已参与；预览、入队和失败均不占链。
                    self._manager.chorus_participation.mark_joined(
                        plan.group_id,
                        plan.move.chorus_chain_id,
                        joined_at=int(self.clock()),
                    )
            except LookupError:
                pass
            if part.receipt is not None:
                delivery_status = {
                    OutboxStatus.SENT: "SENT",
                    OutboxStatus.FAILED: "FAILED",
                    OutboxStatus.UNKNOWN: "UNKNOWN",
                }.get(part.status, "UNKNOWN")
                self._record_trace(
                    self.trace_repository.record_delivery,
                    part.correlation_id,
                    delivery_status,
                    part.receipt.platform_message_id,
                    part.receipt.error_code,
                    int(self.clock()),
                )
            await self._manager.drain()

    def _record_trace(self, operation, *args, **kwargs) -> None:
        try:
            operation(*args, **kwargs)
            self.trace_error = None
        except Exception as exc:
            self.trace_error = f"{type(exc).__name__}: {exc}"

    def _reconcile_shadow_reviews(self) -> None:
        if self._manager is None or self.shadow_reviews is None:
            return
        capture = getattr(self.shadow_reviews, "capture_runtime", None)
        if not callable(capture):
            self.shadow_review_error = "shadow review recorder contract is invalid"
            return
        try:
            pending_evidence = self._manager.pending_shadow_review_evidence()
        except Exception as exc:
            self.shadow_review_error = f"{type(exc).__name__}: {exc}"
            return
        for pending in pending_evidence:
            try:
                capture(pending.evaluation)
                if not self._manager.complete_shadow_review_evidence(
                    pending.capture_id
                ):
                    raise RuntimeError("shadow review evidence acknowledgement failed")
                self.shadow_review_error = None
            except Exception as exc:
                self.shadow_review_error = f"{type(exc).__name__}: {exc}"

    async def close(self) -> None:
        task = self._attention_task
        self._attention_task = None
        if task is not None:
            task.cancel()
            with suppress(BaseException):
                await task
        manager = self._manager
        cognition_client = self._cognition_client
        profile_service = self._profile_service
        knowledge_service = self._knowledge_service
        knowledge_job_service = self._knowledge_job_service
        profile_client = self._profile_client
        self._manager = None
        self._cognition_client = None
        self._profile_service = None
        self._knowledge_service = None
        self._knowledge_job_service = None
        self._knowledge_repository = None
        self._member_style_service = None
        self._imitation_controller = None
        self._profile_client = None
        try:
            await self._close_resources(
                knowledge_job_service,
                self.official_source_probe,
                knowledge_service,
                profile_service,
                manager,
                cognition_client,
                profile_client,
                suppress_errors=False,
            )
        finally:
            self._reply_executor = None
            self._reply_model = None
            self._scene_interpreter = None
            self._dispatcher = None
            self._started = False

    @staticmethod
    async def _close_resources(
        *resources: object | None, suppress_errors: bool
    ) -> None:
        first_error: BaseException | None = None
        for resource in resources:
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None and not suppress_errors:
            raise first_error

    @staticmethod
    def _new_cognition_client(
        settings: SocialRuntimeSettings,
    ) -> DeepSeekCognitionClient:
        return DeepSeekCognitionClient(
            api_key=settings.cognition_api_key,
            api_base=settings.cognition_api_base,
            model=settings.cognition_model,
        )

    @staticmethod
    def _new_member_style_client(
        settings: SocialRuntimeSettings,
    ) -> DeepSeekMemberStyleClient:
        return DeepSeekMemberStyleClient(
            api_key=settings.cognition_api_key,
            api_base=settings.cognition_api_base,
            model=settings.cognition_model,
            timeout_seconds=settings.profile_timeout_seconds,
        )

    @staticmethod
    def _new_profile_client(
        settings: SocialRuntimeSettings,
    ) -> DeepSeekProfileClient:
        return DeepSeekProfileClient(
            api_key=settings.cognition_api_key,
            api_base=settings.cognition_api_base,
            model=settings.cognition_model,
            timeout_seconds=settings.profile_timeout_seconds,
        )
