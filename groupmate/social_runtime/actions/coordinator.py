"""Persistent orchestration for already validated finite ActionPlan DAGs."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping

from ..contracts import SocialEventEnvelope
from ..delivery.outbox import OutboxService
from ..persistence.schema import connect_database, initialize_database
from ..tasks.contracts import (
    ProviderEvent,
    ProviderEventKind,
    TaskRun,
    TaskStatus,
    normalize_provider_event,
)
from ..tasks.runtime import TaskRuntime
from .contracts import (
    ActionEdge,
    ActionNode,
    ActionPlan,
    DeliveryReceipt,
    OutboxPart,
    PlanValidation,
    action_plan_digest,
)


class PlanNotValidated(PermissionError):
    """Raised when execution is attempted without a matching validation result."""


class PlanIdentityConflict(RuntimeError):
    """Raised when a durable plan identity is reused for different content."""


class PlanExecutionConflict(RuntimeError):
    """Raised when another coordinator advances the same plan first."""


class NodeExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PlanExecutionStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


def _json_mapping(value: Mapping[str, object] | None) -> Mapping[str, object]:
    try:
        encoded = json.dumps(
            dict(value or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("node output must be JSON serializable") from exc
    return MappingProxyType(json.loads(encoded))


@dataclass(frozen=True)
class NodeExecutionResult:
    status: NodeExecutionStatus
    output: Mapping[str, object]
    error_code: str | None = None

    def __post_init__(self) -> None:
        status = NodeExecutionStatus(self.status)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "output", _json_mapping(self.output))
        error = None if self.error_code is None else str(self.error_code).strip()
        if status is NodeExecutionStatus.FAILED and not error:
            raise ValueError("failed node execution requires an error code")
        if status is not NodeExecutionStatus.FAILED and error:
            raise ValueError("only failed node execution may carry an error code")
        object.__setattr__(self, "error_code", error)

    @classmethod
    def succeeded(
        cls, output: Mapping[str, object] | None = None
    ) -> "NodeExecutionResult":
        return cls(NodeExecutionStatus.SUCCEEDED, _json_mapping(output))

    @classmethod
    def waiting(
        cls, output: Mapping[str, object] | None = None
    ) -> "NodeExecutionResult":
        return cls(NodeExecutionStatus.WAITING, _json_mapping(output))

    @classmethod
    def failed(cls, error_code: str) -> "NodeExecutionResult":
        code = str(error_code).strip()
        if not code:
            raise ValueError("failed node execution requires an error code")
        return cls(NodeExecutionStatus.FAILED, _json_mapping(None), code)


@dataclass(frozen=True)
class NodeExecutionState:
    node_id: str
    status: NodeExecutionStatus
    attempts: int
    output: Mapping[str, object]
    error_code: str | None
    updated_at: int


@dataclass(frozen=True)
class PlanExecution:
    plan: ActionPlan
    status: PlanExecutionStatus
    node_states: tuple[NodeExecutionState, ...]
    runnable_node_ids: tuple[str, ...]
    pending_feedback: tuple[Mapping[str, object], ...]
    version: int
    updated_at: int

    def node_state(self, node_id: str) -> NodeExecutionState:
        for state in self.node_states:
            if state.node_id == node_id:
                return state
        raise KeyError(node_id)


NodeExecutor = Callable[
    [ActionPlan, ActionNode, Mapping[str, Mapping[str, object]], int],
    Awaitable[NodeExecutionResult],
]
EventSink = Callable[[SocialEventEnvelope], Awaitable[object]]

_TASK_EVENT_NODE_KINDS = frozenset(
    {
        "REQUEST_CONFIRMATION",
        "WAIT_TASK_EVENT",
        "RENDER_PROGRESS",
        "RENDER_RESULT",
    }
)
_SAFE_AUTOMATIC_RETRY_KINDS = frozenset(
    {
        "GENERATE_TEXT",
        "SELECT_REACTION",
        "SELECT_MEDIA",
        "RENDER_PROGRESS",
        "RENDER_RESULT",
        "RECORD_OBSERVATION",
    }
)


class ExecutionCoordinator:
    """Advances durable node state without owning Actor, Task, or Outbox state."""

    def __init__(
        self,
        path: Path,
        *,
        executors: Mapping[str, NodeExecutor] | None = None,
        task_runtime: TaskRuntime | None = None,
        outbox: OutboxService | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.executors = dict(executors or {})
        self.task_runtime = task_runtime or TaskRuntime(self.path)
        self.outbox = outbox or OutboxService(self.path)
        self._event_sink = event_sink

    def submit(
        self,
        plan: ActionPlan,
        validation: PlanValidation,
        *,
        now: int,
    ) -> PlanExecution:
        if (
            not isinstance(validation, PlanValidation)
            or not validation.accepted
            or validation.plan_id != plan.plan_id
            or validation.plan_digest != action_plan_digest(plan)
        ):
            raise PlanNotValidated("coordinator requires validation for this exact plan")
        if plan.expires_at <= now:
            raise PlanNotValidated("validated plan has expired before execution")
        states = tuple(
            NodeExecutionState(
                node_id=node.node_id,
                status=NodeExecutionStatus.PENDING,
                attempts=0,
                output=_json_mapping(None),
                error_code=None,
                updated_at=int(now),
            )
            for node in plan.nodes
        )
        execution = PlanExecution(
            plan=plan,
            status=PlanExecutionStatus.READY,
            node_states=states,
            runnable_node_ids=self._runnable(plan, states),
            pending_feedback=(
                self._feedback(
                    event_id=f"plan-feedback:{plan.plan_id}:accepted",
                    event_type="plan.accepted",
                    occurred_at=now,
                    payload={"plan_id": plan.plan_id, "status": "ready"},
                ),
            ),
            version=1,
            updated_at=int(now),
        )
        encoded = self._encode(execution)
        with closing(connect_database(self.path)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT plan_json FROM action_plans WHERE plan_id=?",
                    (plan.plan_id,),
                ).fetchone()
                if row is not None:
                    existing = self._decode(str(row["plan_json"]))
                    if existing.plan != plan:
                        raise PlanIdentityConflict(
                            f"plan identity was reused: {plan.plan_id}"
                        )
                    db.commit()
                    return existing
                db.execute(
                    "INSERT INTO action_plans(plan_id, correlation_id, persona_id, "
                    "group_id, scene_version, status, plan_json, expires_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan.plan_id,
                        plan.correlation_id,
                        plan.persona_id,
                        plan.group_id,
                        plan.scene_version,
                        execution.status.value,
                        encoded,
                        plan.expires_at,
                    ),
                )
                db.commit()
                return execution
            except BaseException:
                db.rollback()
                raise

    def load(self, plan_id: str) -> PlanExecution:
        with closing(connect_database(self.path)) as db:
            row = db.execute(
                "SELECT plan_json FROM action_plans WHERE plan_id=?", (plan_id,)
            ).fetchone()
        if row is None:
            raise LookupError(plan_id)
        return self._decode(str(row["plan_json"]))

    async def advance(self, plan_id: str, *, now: int) -> PlanExecution:
        execution = await self._flush_feedback(self.load(plan_id))
        if execution.status in {
            PlanExecutionStatus.COMPLETED,
            PlanExecutionStatus.FAILED,
            PlanExecutionStatus.EXPIRED,
        }:
            return execution
        if execution.plan.expires_at <= now:
            expired = replace(
                execution,
                status=PlanExecutionStatus.EXPIRED,
                runnable_node_ids=(),
                pending_feedback=execution.pending_feedback
                + (
                    self._feedback(
                        event_id=f"plan-feedback:{plan_id}:expired",
                        event_type="plan.expired",
                        occurred_at=now,
                        payload={"plan_id": plan_id, "status": "expired"},
                    ),
                ),
                version=execution.version + 1,
                updated_at=int(now),
            )
            self._save(execution, expired)
            return await self._flush_feedback(expired)

        expired_node_ids = {
            node.node_id
            for node in execution.plan.nodes
            if node.node_id in execution.runnable_node_ids
            and node.deadline_at is not None
            and node.deadline_at <= now
        }
        if expired_node_ids:
            states = tuple(
                replace(
                    state,
                    status=NodeExecutionStatus.FAILED,
                    error_code="node_deadline_expired",
                    updated_at=int(now),
                )
                if state.node_id in expired_node_ids
                else state
                for state in execution.node_states
            )
            deadline_failed = replace(
                execution,
                status=PlanExecutionStatus.FAILED,
                node_states=states,
                runnable_node_ids=(),
                pending_feedback=execution.pending_feedback
                + tuple(
                    self._feedback(
                        event_id=f"plan-feedback:{plan_id}:{node_id}:deadline",
                        event_type="plan.node_failed",
                        occurred_at=now,
                        payload={
                            "plan_id": plan_id,
                            "node_id": node_id,
                            "status": "failed",
                            "error_code": "node_deadline_expired",
                        },
                    )
                    for node_id in sorted(expired_node_ids)
                ),
                version=execution.version + 1,
                updated_at=int(now),
            )
            self._save(execution, deadline_failed)
            return await self._flush_feedback(deadline_failed)

        selected_ids = execution.runnable_node_ids[: execution.plan.concurrency]
        if not selected_ids:
            return execution
        selected = tuple(
            node for node in execution.plan.nodes if node.node_id in selected_ids
        )
        state_by_id = {state.node_id: state for state in execution.node_states}
        for node in selected:
            prior = state_by_id[node.node_id]
            state_by_id[node.node_id] = replace(
                prior,
                status=NodeExecutionStatus.RUNNING,
                attempts=prior.attempts + 1,
                error_code=None,
                updated_at=int(now),
            )
        running_states = self._ordered_states(execution.plan, state_by_id)
        running = replace(
            execution,
            status=PlanExecutionStatus.RUNNING,
            node_states=running_states,
            runnable_node_ids=self._runnable(execution.plan, running_states),
            version=execution.version + 1,
            updated_at=int(now),
        )
        self._save(execution, running)

        results = await asyncio.gather(
            *(
                self._execute_node(running, node, int(now))
                for node in selected
            ),
            return_exceptions=True,
        )
        updated_by_id = {state.node_id: state for state in running.node_states}
        feedback = list(running.pending_feedback)
        for node, raw_result in zip(selected, results):
            result = (
                NodeExecutionResult.failed(type(raw_result).__name__)
                if isinstance(raw_result, BaseException)
                else raw_result
            )
            if not isinstance(result, NodeExecutionResult):
                result = NodeExecutionResult.failed("invalid_node_result")
            prior = updated_by_id[node.node_id]
            target = result.status
            if (
                target is NodeExecutionStatus.FAILED
                and prior.attempts <= node.retry_limit
                and node.kind in _SAFE_AUTOMATIC_RETRY_KINDS
                and (node.deadline_at is None or now < node.deadline_at)
            ):
                target = NodeExecutionStatus.PENDING
            updated_by_id[node.node_id] = replace(
                prior,
                status=target,
                output=_json_mapping(result.output),
                error_code=result.error_code,
                updated_at=int(now),
            )
            feedback.append(
                self._feedback(
                    event_id=(
                        f"plan-feedback:{running.plan.plan_id}:{node.node_id}:"
                        f"{prior.attempts}:{result.status.value}"
                    ),
                    event_type=f"plan.node_{result.status.value}",
                    occurred_at=now,
                    payload={
                        "plan_id": running.plan.plan_id,
                        "node_id": node.node_id,
                        "node_kind": node.kind,
                        "status": result.status.value,
                        "error_code": result.error_code,
                    },
                )
            )
        final_states = self._ordered_states(running.plan, updated_by_id)
        if all(
            state.status is NodeExecutionStatus.SUCCEEDED
            for state in final_states
        ):
            status = PlanExecutionStatus.COMPLETED
            feedback.append(
                self._feedback(
                    event_id=f"plan-feedback:{running.plan.plan_id}:completed",
                    event_type="plan.completed",
                    occurred_at=now,
                    payload={"plan_id": running.plan.plan_id, "status": "completed"},
                )
            )
        elif any(
            state.status is NodeExecutionStatus.FAILED for state in final_states
        ):
            status = PlanExecutionStatus.FAILED
        else:
            status = PlanExecutionStatus.RUNNING
        completed = replace(
            running,
            status=status,
            node_states=final_states,
            runnable_node_ids=self._runnable(running.plan, final_states),
            pending_feedback=tuple(feedback),
            version=running.version + 1,
            updated_at=int(now),
        )
        self._save(running, completed)
        return await self._flush_feedback(completed)

    async def apply_provider_event(self, event: ProviderEvent) -> TaskRun:
        event = normalize_provider_event(event)
        task = self.task_runtime.apply_event(event)
        self._wake_task_nodes(task, event.kind, now=event.occurred_at)
        await self._publish_provider_feedback(event, task)
        return task

    async def _publish_provider_feedback(
        self, event: ProviderEvent, task: TaskRun
    ) -> None:
        event_type = {
            ProviderEventKind.ACCEPTED: "capability.accepted",
            ProviderEventKind.PROGRESS: "capability.progress",
            ProviderEventKind.SUCCEEDED: "capability.result",
            ProviderEventKind.FAILED: "capability.result",
            ProviderEventKind.CANCELED: "capability.result",
        }[event.kind]
        payload = {
            "provider_event_id": event.event_id,
            "provider_event_kind": event.kind.value,
            "task_id": task.task_id,
            "task_status": {
                ProviderEventKind.ACCEPTED: TaskStatus.RUNNING.value,
                ProviderEventKind.PROGRESS: TaskStatus.RUNNING.value,
                ProviderEventKind.SUCCEEDED: TaskStatus.SUCCEEDED.value,
                ProviderEventKind.FAILED: TaskStatus.FAILED.value,
                ProviderEventKind.CANCELED: TaskStatus.CANCELED.value,
            }[event.kind],
            "requester_id": task.requester_id,
            "topic_id": task.topic_id,
            "direct_request": task.request.direct_request,
            "delivery_relevant": task.delivery_relevant,
            "expires_at": task.expires_at,
            "progress": event.progress,
            "result": None if event.result is None else dict(event.result),
            "result_media": [asdict(item) for item in event.media],
            "error_code": event.error_code,
        }
        await self._emit_direct(
            event_id=f"provider-feedback:{event.event_id}",
            event_type=event_type,
            occurred_at=event.occurred_at,
            persona_id=task.persona_id,
            group_id=task.group_id,
            actor_id=task.requester_id,
            correlation_id=task.correlation_id,
            causation_id=event.event_id,
            payload=payload,
        )

    async def confirm_task(
        self, task_id: str, *, confirmer_id: str, now: int
    ) -> TaskRun:
        task = self.task_runtime.confirm(task_id, confirmer_id=confirmer_id, now=now)
        self._wake_task_nodes(task, "confirmed", now=now)
        await self._emit_direct(
            event_id=f"task-feedback:{task.task_id}:confirmed:{task.version}",
            event_type="capability.confirmed",
            occurred_at=now,
            persona_id=task.persona_id,
            group_id=task.group_id,
            actor_id=confirmer_id,
            correlation_id=task.correlation_id,
            causation_id=None,
            payload={
                "task_id": task.task_id,
                "task_status": task.status.value,
                "requester_id": task.requester_id,
                "topic_id": task.topic_id,
                "direct_request": task.request.direct_request,
            },
        )
        return task

    async def apply_delivery_receipt(self, receipt: DeliveryReceipt) -> OutboxPart:
        part = self.outbox.record_receipt(receipt)
        assert part.receipt is not None
        await self._publish_delivery_feedback(part.receipt, part)
        return part

    async def _publish_delivery_feedback(
        self, receipt: DeliveryReceipt, part: OutboxPart
    ) -> None:
        await self._emit_direct(
            event_id=f"delivery-feedback:{receipt.receipt_id}",
            event_type=f"delivery.{part.status.value}",
            occurred_at=receipt.occurred_at,
            persona_id=part.persona_id,
            group_id=part.group_id,
            actor_id=None,
            correlation_id=part.correlation_id,
            causation_id=receipt.receipt_id,
            payload={
                "receipt_id": receipt.receipt_id,
                "part_id": part.part_id,
                "bundle_id": part.bundle_id,
                "delivery_status": part.status.value,
                "topic_id": part.topic_id,
                "error_code": receipt.error_code,
            },
        )

    async def recover_feedback(self) -> None:
        with closing(connect_database(self.path)) as db:
            rows = db.execute(
                "SELECT plan_json FROM action_plans ORDER BY rowid"
            ).fetchall()
        for row in rows:
            await self._flush_feedback(self._decode(str(row["plan_json"])))
        for event in self.task_runtime.provider_events():
            await self._publish_provider_feedback(
                event, self.task_runtime.load(event.task_id)
            )
        for part in self.outbox.receipted_parts():
            assert part.receipt is not None
            await self._publish_delivery_feedback(part.receipt, part)

    async def _execute_node(
        self, execution: PlanExecution, node: ActionNode, now: int
    ) -> NodeExecutionResult:
        executor = self.executors.get(node.kind)
        if executor is None:
            return NodeExecutionResult.failed("executor_not_registered")
        dependencies = {
            edge.source_node_id: execution.node_state(edge.source_node_id).output
            for edge in execution.plan.edges
            if edge.target_node_id == node.node_id
        }
        return await executor(execution.plan, node, dependencies, now)

    async def _flush_feedback(self, execution: PlanExecution) -> PlanExecution:
        if self._event_sink is None:
            return execution
        current = execution
        while current.pending_feedback:
            raw = current.pending_feedback[0]
            await self._emit_direct(
                event_id=str(raw["event_id"]),
                event_type=str(raw["event_type"]),
                occurred_at=int(raw["occurred_at"]),
                persona_id=current.plan.persona_id,
                group_id=current.plan.group_id,
                actor_id=None,
                correlation_id=current.plan.correlation_id,
                causation_id=current.plan.plan_id,
                payload=dict(raw["payload"]),
            )
            updated = replace(
                current,
                pending_feedback=current.pending_feedback[1:],
                version=current.version + 1,
            )
            self._save(current, updated)
            current = updated
        return current

    async def _emit_direct(self, **values: object) -> None:
        if self._event_sink is None:
            return
        event = SocialEventEnvelope.create(
            event_id=values["event_id"],
            event_type=values["event_type"],
            occurred_at=values["occurred_at"],
            received_at=values["occurred_at"],
            persona_id=values["persona_id"],
            group_id=values["group_id"],
            actor_id=values["actor_id"],
            source_message_id=None,
            correlation_id=values["correlation_id"],
            causation_id=values["causation_id"],
            payload=values["payload"],
        )
        await self._event_sink(event)

    def _wake_task_nodes(
        self, task: TaskRun, kind: object, *, now: int
    ) -> None:
        del kind
        with closing(connect_database(self.path)) as db:
            rows = db.execute("SELECT plan_json FROM action_plans").fetchall()
        for row in rows:
            execution = self._decode(str(row["plan_json"]))
            if not any(
                state.output.get("task_id") == task.task_id
                for state in execution.node_states
            ):
                continue
            states = {
                state.node_id: (
                    replace(
                        state,
                        status=NodeExecutionStatus.PENDING,
                        updated_at=int(now),
                    )
                    if state.status is NodeExecutionStatus.WAITING
                    and next(
                        node.kind
                        for node in execution.plan.nodes
                        if node.node_id == state.node_id
                    )
                    in _TASK_EVENT_NODE_KINDS
                    else state
                )
                for state in execution.node_states
            }
            ordered = self._ordered_states(execution.plan, states)
            updated = replace(
                execution,
                node_states=ordered,
                runnable_node_ids=self._runnable(execution.plan, ordered),
                version=execution.version + 1,
                updated_at=int(now),
            )
            self._save(execution, updated)

    def _save(self, previous: PlanExecution, updated: PlanExecution) -> None:
        before = self._encode(previous)
        after = self._encode(updated)
        with closing(connect_database(self.path)) as db:
            changed = db.execute(
                "UPDATE action_plans SET status=?, plan_json=? "
                "WHERE plan_id=? AND plan_json=?",
                (
                    updated.status.value,
                    after,
                    updated.plan.plan_id,
                    before,
                ),
            ).rowcount
            db.commit()
        if changed != 1:
            raise PlanExecutionConflict(
                f"plan advanced concurrently: {updated.plan.plan_id}"
            )

    @staticmethod
    def _runnable(
        plan: ActionPlan, states: tuple[NodeExecutionState, ...]
    ) -> tuple[str, ...]:
        by_id = {state.node_id: state for state in states}
        predecessors = {node.node_id: set() for node in plan.nodes}
        for edge in plan.edges:
            predecessors[edge.target_node_id].add(edge.source_node_id)
        return tuple(
            sorted(
                node.node_id
                for node in plan.nodes
                if by_id[node.node_id].status is NodeExecutionStatus.PENDING
                and all(
                    by_id[source].status is NodeExecutionStatus.SUCCEEDED
                    for source in predecessors[node.node_id]
                )
            )
        )

    @staticmethod
    def _ordered_states(
        plan: ActionPlan, states: Mapping[str, NodeExecutionState]
    ) -> tuple[NodeExecutionState, ...]:
        return tuple(states[node.node_id] for node in plan.nodes)

    @staticmethod
    def _feedback(**values: object) -> Mapping[str, object]:
        return _json_mapping(values)

    @staticmethod
    def _plan_to_dict(plan: ActionPlan) -> dict[str, object]:
        return asdict(plan)

    @staticmethod
    def _plan_from_dict(value: Mapping[str, object]) -> ActionPlan:
        values = dict(value)
        values["intention_ids"] = tuple(values["intention_ids"])
        values["audience"] = tuple(values["audience"])
        values["nodes"] = tuple(ActionNode(**item) for item in values["nodes"])
        values["edges"] = tuple(ActionEdge(**item) for item in values["edges"])
        for name in (
            "constraints",
            "media_references",
            "confirmation_ids",
        ):
            values[name] = tuple(values[name])
        return ActionPlan(**values)

    @classmethod
    def _encode(cls, execution: PlanExecution) -> str:
        payload = {
            "plan": cls._plan_to_dict(execution.plan),
            "status": execution.status.value,
            "node_states": [
                {
                    "node_id": state.node_id,
                    "status": state.status.value,
                    "attempts": state.attempts,
                    "output": dict(state.output),
                    "error_code": state.error_code,
                    "updated_at": state.updated_at,
                }
                for state in execution.node_states
            ],
            "runnable_node_ids": list(execution.runnable_node_ids),
            "pending_feedback": [dict(item) for item in execution.pending_feedback],
            "version": execution.version,
            "updated_at": execution.updated_at,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def _decode(cls, encoded: str) -> PlanExecution:
        value = json.loads(encoded)
        return PlanExecution(
            plan=cls._plan_from_dict(value["plan"]),
            status=PlanExecutionStatus(value["status"]),
            node_states=tuple(
                NodeExecutionState(
                    node_id=item["node_id"],
                    status=NodeExecutionStatus(item["status"]),
                    attempts=int(item["attempts"]),
                    output=_json_mapping(item.get("output")),
                    error_code=item.get("error_code"),
                    updated_at=int(item["updated_at"]),
                )
                for item in value["node_states"]
            ),
            runnable_node_ids=tuple(value["runnable_node_ids"]),
            pending_feedback=tuple(
                _json_mapping(item) for item in value.get("pending_feedback", ())
            ),
            version=int(value["version"]),
            updated_at=int(value["updated_at"]),
        )


__all__ = (
    "ExecutionCoordinator",
    "NodeExecutionResult",
    "NodeExecutionState",
    "NodeExecutionStatus",
    "PlanExecution",
    "PlanExecutionConflict",
    "PlanExecutionStatus",
    "PlanIdentityConflict",
    "PlanNotValidated",
)
