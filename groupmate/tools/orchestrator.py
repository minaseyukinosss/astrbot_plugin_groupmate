"""Groupmate-owned natural-language tool execution flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

from ..engine.delivery import build_delivery_plan
from ..models import ReplyMode, TriggerKind, Urgency
from .catalog import UniversalToolCatalog
from .contracts import ToolDescriptor, ToolExecutionStatus
from .executor import HostToolExecutor
from .planning import AstrBotToolPersonaRenderer, AstrBotToolPlanner
from .policy import ToolPolicyEngine


_USER_ARG_NAMES = (
    "user_id",
    "target_id",
    "target_user_id",
    "to_qq",
    "qq",
    "uid",
    "member_id",
)


@dataclass(frozen=True)
class _PendingPlan:
    tool_id: str
    arguments: dict[str, Any]
    missing_arguments: tuple[str, ...]
    expires_at: int


class GroupmateToolOrchestrator:
    def __init__(
        self,
        *,
        catalog: UniversalToolCatalog,
        planner: AstrBotToolPlanner,
        renderer: AstrBotToolPersonaRenderer,
        executor: HostToolExecutor,
        policy: ToolPolicyEngine,
        candidate_limit: int = 8,
        enabled: bool = True,
    ) -> None:
        self.catalog = catalog
        self.planner = planner
        self.renderer = renderer
        self.executor = executor
        self.policy = policy
        self.candidate_limit = max(1, min(20, int(candidate_limit)))
        self.enabled = bool(enabled)
        self._pending: dict[tuple[str, str], _PendingPlan] = {}

    def has_candidate(self, message: str) -> bool:
        if not self.enabled:
            return False
        self.catalog.refresh()
        return bool(
            self.catalog.retrieve(
                message,
                limit=self.candidate_limit,
                min_score=10,
            )
        )

    async def try_handle(self, event: Any, actor: Any, message: Any) -> bool:
        if not self.enabled:
            return False
        pending_key = (str(message.group_id), str(message.sender_id))
        pending = self._pending.get(pending_key)
        if pending is not None and int(message.timestamp) > pending.expires_at:
            self._pending.pop(pending_key, None)
            pending = None
        trigger = actor.router.classify(message)
        if pending is None and trigger.kind not in (
            TriggerKind.NATIVE_DIRECT,
            TriggerKind.ALIAS_DIRECT,
            TriggerKind.CONTINUATION,
        ):
            return False

        self.catalog.refresh()
        if pending is not None:
            pending_descriptor = self.catalog.get(pending.tool_id)
            candidates = (pending_descriptor,) if pending_descriptor is not None else ()
        else:
            candidates = self.catalog.retrieve(
                message.text,
                limit=self.candidate_limit,
            )
        if not candidates:
            self._pending.pop(pending_key, None)
            return False
        await self._preload(actor, message)
        mentions = tuple(
            str(item).strip()
            for item in (getattr(message, "mentioned_user_ids", ()) or ())
            if str(item).strip()
        )
        planner_message = _planner_message_text(message.text, mentions)
        if pending is not None:
            planner_message = (
                "这是上次请求的信息补充。待处理事项：{}；已有信息：{}；"
                "仍缺少：{}；用户本次补充：{}"
            ).format(
                candidates[0].description or candidates[0].name,
                pending.arguments,
                "、".join(pending.missing_arguments),
                planner_message,
            )
        recent = [
            item.text
            for item in actor.window.snapshot().messages[-8:]
            if getattr(item, "text", "")
        ]
        plan = await self.planner.plan(
            group_id=message.group_id,
            message=planner_message,
            candidates=candidates,
            recent_context=recent,
        )
        if not plan.selected:
            return False
        descriptor = self.catalog.get(plan.tool_id)
        if descriptor is None:
            return False
        arguments = dict(pending.arguments) if pending is not None else {}
        arguments.update(plan.arguments)
        arguments = _inject_mention_arguments(descriptor, arguments, mentions)
        # Only block on truly required params. Optional defaults (e.g. days=None)
        # must not trigger clarification even if the planner asks for them.
        required = set(descriptor.required_parameters)
        missing_arguments = tuple(
            sorted(
                name
                for name in required
                if arguments.get(name) in (None, "")
            )
        )

        self._claim_event(event)
        try:
            if missing_arguments:
                expires_at = int(message.timestamp) + max(
                    30,
                    int(actor.behavior.conversation.continuation_seconds),
                )
                self._pending[pending_key] = _PendingPlan(
                    descriptor.tool_id,
                    arguments,
                    missing_arguments,
                    expires_at,
                )
                actor.set_continuation(message.sender_id, expires_at)
                text = await self.renderer.clarification(
                    message.group_id,
                    descriptor,
                    missing_arguments,
                )
                await self._deliver(
                    actor,
                    message,
                    text,
                    kind="tool_clarification",
                    reply_mode=ReplyMode.SHORT_SOCIAL,
                )
                return True

            self._clear_pending(actor, pending_key, message.sender_id)
            authorization = self.policy.authorize(descriptor, event)
            if not authorization.allowed:
                text = await self.renderer.denied(message.group_id, descriptor)
                await self._deliver(
                    actor,
                    message,
                    text,
                    kind="tool_denied",
                    reply_mode=ReplyMode.BOUNDARY,
                )
                return True

            progress_note = ""
            if descriptor.tool_id == "builtin:send_qq_mail":
                from ..mail.service import preview_mail_route

                if preview_mail_route(event, arguments) == "unauthorized":
                    progress_note = (
                        "群友想给别人寄邮件但没有代寄权限。"
                        "等待句要俏皮、像准备整蛊，"
                        "严禁说「寄过去」「帮他寄」「写好寄给对方」这类话；"
                        "禁止反问。"
                    )
            try:
                progress = await self.renderer.progress(
                    message.group_id,
                    descriptor,
                    message.text,
                    note=progress_note,
                )
            except TypeError:
                progress = await self.renderer.progress(
                    message.group_id,
                    descriptor,
                    message.text,
                )
            await self._deliver(
                actor,
                message,
                progress,
                kind="tool_progress",
                reply_mode=ReplyMode.SHORT_SOCIAL,
                quote=False,
            )
            result = await self.executor.execute(
                descriptor,
                arguments,
                event,
            )
            if result.direct_sent:
                return True
            # Empty SUCCESS (e.g. image sent via platform adapter) still gets a
            # short confirm; renderer.final already avoids "blank/empty" wording.
            final_text = await self.renderer.final(
                message.group_id,
                descriptor,
                result,
            )
            await self._deliver(
                actor,
                message,
                final_text,
                kind="tool_result",
                reply_mode=(
                    ReplyMode.TASK_RESULT
                    if result.status is ToolExecutionStatus.SUCCESS
                    else ReplyMode.BOUNDARY
                ),
            )
            return True
        except Exception:
            self._clear_pending(actor, pending_key, message.sender_id)
            try:
                await self._deliver(
                    actor,
                    message,
                    "这次没弄成，晚点再试吧。",
                    kind="tool_failed",
                    reply_mode=ReplyMode.BOUNDARY,
                )
            except Exception:
                pass
            return True

    @staticmethod
    def _claim_event(event: Any) -> None:
        marker = getattr(event, "should_call_llm", None)
        if callable(marker):
            marker(True)
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()

    async def _deliver(
        self,
        actor: Any,
        message: Any,
        text: str,
        *,
        kind: str,
        reply_mode: ReplyMode,
        quote: bool = True,
    ) -> Any:
        cleaned = await self._guard(actor, text, reply_mode)
        now = actor.workflow.clock.now()
        plan = build_delivery_plan(
            decision_id="tool-" + uuid4().hex,
            group_id=message.group_id,
            text=cleaned,
            urgency=Urgency.HIGH,
            now=now,
            ttl_seconds=120,
            max_chars=500 if reply_mode is ReplyMode.TASK_RESULT else 120,
            max_segments=3 if reply_mode is ReplyMode.TASK_RESULT else 1,
            humanize_delay=False,
            direct_wake=True,
            quote_message_id=message.message_id if quote else None,
        )
        outcome = await actor.workflow.delivery_service.deliver(plan, kind=kind)
        actor.last_outcome = outcome
        if outcome.sent and outcome.text:
            actor.workflow._recent_outputs[actor.group_id].append(outcome.text)
            projector = getattr(actor, "_append_bot_projection", None)
            if callable(projector):
                projector(outcome)
        return outcome

    @staticmethod
    async def _preload(actor: Any, message: Any) -> None:
        preload = getattr(actor, "preload", None)
        if not callable(preload):
            return
        await preload(message)
        drain = getattr(actor, "drain", None)
        if callable(drain):
            await drain()

    def _clear_pending(
        self,
        actor: Any,
        key: tuple[str, str],
        sender_id: str,
    ) -> None:
        self._pending.pop(key, None)
        setter = getattr(actor, "set_continuation", None)
        if callable(setter):
            setter(sender_id, 0)

    @staticmethod
    async def _guard(actor: Any, text: str, reply_mode: ReplyMode) -> str:
        fallback = (
            "这次没弄成。"
            if reply_mode is ReplyMode.BOUNDARY
            else "我去看看，稍等一下。"
        )
        guard = actor.workflow.output_guard
        recent = tuple(actor.workflow._recent_outputs[actor.group_id])
        checked = guard.validate(
            text,
            recent,
            reply_mode=reply_mode,
        )
        if checked.accepted:
            return checked.text
        if checked.repairable:
            try:
                repaired = await actor.workflow.generation_model.repair(
                    checked.text,
                    checked.codes,
                )
                rechecked = guard.validate(
                    repaired,
                    recent,
                    reply_mode=reply_mode,
                )
                if rechecked.accepted:
                    return rechecked.text
            except Exception:
                pass
        return fallback


def _planner_message_text(text: str, mentions: Sequence[str]) -> str:
    body = str(text or "").strip()
    if not mentions:
        return body
    mention_line = "消息中@的用户ID：" + "、".join(mentions)
    if not body:
        return mention_line
    return body + "\n" + mention_line


def _inject_mention_arguments(
    descriptor: ToolDescriptor,
    arguments: Mapping[str, Any],
    mentions: Sequence[str],
) -> dict[str, Any]:
    cleaned = dict(arguments or {})
    if len(mentions) != 1:
        return cleaned
    properties = descriptor.parameters.get("properties")
    if not isinstance(properties, dict):
        return cleaned
    target = str(mentions[0])
    for name in _USER_ARG_NAMES:
        if name not in properties:
            continue
        if cleaned.get(name) in (None, ""):
            cleaned[name] = target
    return cleaned
