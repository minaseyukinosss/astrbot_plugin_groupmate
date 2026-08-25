"""Durable, privacy-trimmed message lifecycle traces for the plugin page."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from ...adapters.participants import ParticipantDirectory
from ...adapters.message_media import MessageMediaDirectory
from ..contracts import SocialEventEnvelope
from ..persistence.schema import connect_database, initialize_database


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_TRIGGER_LABELS = {
    "AMBIENT": "观察群聊上下文",
    "CONTINUATION": "延续当前对话",
    "FAST": "直接请求或高优先级事件",
    "TEMPORAL": "定时事项到期",
    "AUTONOMOUS": "主动参与机会",
}
_OUTCOME_LABELS = {
    "ACT": "准备回复",
    "DEFER": "稍后再判断",
    "OBSERVE": "继续观察",
    "SILENCE": "保持沉默",
    "PENDING": "等待判断",
}
_REASON_LABELS = {
    "forced_observe": "认知降级或参与条件不足",
    "no_eligible_intention": "没有足够合适的参与意图",
    "observe_intention": "当前更适合继续观察",
    "utility_below_threshold": "参与价值未达到阈值",
    "rate_limited": "触发频率限制",
    "paused": "运行已暂停",
    "platform_unavailable": "平台当前不可用",
    "privacy_blocked": "隐私规则不允许处理",
}
_STAGE_ORDER = {
    "RECEIVED": 10,
    "ROUTED": 20,
    "ATTENDED": 30,
    "UNDERSTOOD": 40,
    "DECIDED": 50,
    "PLANNED": 60,
    "DELIVERED": 70,
}
_ADDRESS_KINDS = {
    "AT",
    "REPLY",
    "PURE_ALIAS",
    "ALIAS_PREFIX",
    "ALIAS_SUFFIX",
}


def _direct_reason(event: SocialEventEnvelope) -> str:
    kind = str(event.payload.get("address_kind") or "").upper()
    if kind == "AT":
        return "明确 @ 机器人"
    if kind == "REPLY":
        return "回复了 Bot 的上一条消息"
    alias = " ".join(str(event.payload.get("matched_alias") or "").split())[:24]
    if kind in {"PURE_ALIAS", "ALIAS_PREFIX", "ALIAS_SUFFIX"} and alias:
        return f"命中人格别称：{alias}"
    return "明确对 Bot 发起互动"


class MessageTraceRepository:
    """Maintains one public trace row for each incoming platform message."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.participants = ParticipantDirectory(
            self.path,
            self.path.parent / "avatars",
        )
        self.media = MessageMediaDirectory(
            self.path,
            self.path.parent / "message-media",
        )
        self._ensure_tables()

    def record_received(
        self,
        event: SocialEventEnvelope,
        runtime_mode: object,
        now: int,
    ) -> None:
        if event.event_type != "platform.message" or not event.group_id:
            return
        now = int(now)
        mode = self._mode_value(runtime_mode)
        self._remember_segment_participants(event)
        participant = self.participants.remember(event)
        owner = str(event.payload.get("interaction_owner") or "UNKNOWN").upper()
        external = owner == "EXTERNAL_PLUGIN"
        route = (
            {
                "owner": "EXTERNAL_PLUGIN",
                "label": "交给外部能力",
                "reason": "匹配已配置的外部触发规则",
            }
            if external
            else {
                "owner": "PENDING",
                "label": "等待 AstrBot 路由",
                "reason": "消息已到达，等待插件处理顺序确认",
            }
        )
        address_evidence = self._address_evidence(event)
        if address_evidence:
            route.update(address_evidence)
            if not external:
                route["reason"] = str(address_evidence["address_reason"])
        delivery = (
            {
                "mode": mode,
                "status": "HANDED_OFF",
                "label": "结果由外部插件负责",
            }
            if external
            else {"mode": mode, "status": "RECEIVED", "label": "等待处理"}
        )
        message_parts = self.media.remember(
            event,
            mention_resolver=lambda actor_id: self.participants.resolve_actor(
                persona_id=event.persona_id,
                group_id=event.group_id or "",
                actor_id=actor_id,
            ),
        )
        summary = {
            "actor": {
                "member_ref": participant["member_ref"],
                "display_name": participant["display_name"],
                "avatar_ref": participant["avatar_ref"],
            },
            "message": {
                "summary": self.media.summary(message_parts),
                "media_types": [
                    str(part.get("kind"))
                    for part in message_parts
                    if part.get("kind") != "text"
                ][:8],
                "parts": message_parts,
            },
            "route": route,
            "understanding": {"status": "PENDING", "summary": "尚未进入理解链路"},
            "decision": {"outcome": "PENDING", "label": "等待判断", "reasons": []},
            "delivery": delivery,
            "timing": {"received_at": now, "updated_at": now, "total_ms": 0},
            "stages": [],
        }
        stage = {
            "kind": "RECEIVED",
            "label": "NapCat 消息已到达 AstrBot",
            "at": now,
            "status": "DONE",
        }
        self._create_or_update(event, summary, stage, now)

    def _remember_segment_participants(self, event: SocialEventEnvelope) -> None:
        segments = event.payload.get("segments")
        if not isinstance(segments, (list, tuple)) or not event.group_id:
            return
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            kind = str(segment.get("type") or "").lower()
            data = segment.get("data")
            if not isinstance(data, Mapping):
                continue
            if kind == "at":
                actor_id = str(data.get("qq") or "").strip()
                display_name = str(data.get("name") or "").strip()
                if actor_id.lower() == "all":
                    continue
            elif kind == "reply":
                actor_id = str(
                    data.get("sender_id") or data.get("qq") or ""
                ).strip()
                display_name = str(
                    data.get("sender_nickname") or ""
                ).strip()
            else:
                continue
            if actor_id and display_name:
                self.participants.remember_actor(
                    persona_id=event.persona_id,
                    group_id=event.group_id,
                    actor_id=actor_id,
                    display_name=display_name,
                    updated_at=int(event.received_at),
                )

    def mark_entered(self, event_id: str, now: int) -> None:
        def mutate(summary: dict[str, object]) -> None:
            current = summary.get("route")
            current_route = current if isinstance(current, Mapping) else {}
            evidence = {
                key: current_route[key]
                for key in (
                    "address_kind",
                    "matched_alias",
                    "addressed_to_bot",
                    "alias_candidate",
                    "address_reason",
                )
                if key in current_route
            }
            summary["route"] = {
                "owner": "GROUPMATE",
                "label": "进入 Groupmate",
                "reason": evidence.get(
                    "address_reason", "未被前置命令或外部插件截获"
                ),
                **evidence,
            }

        self._mutate(
            event_id=event_id,
            now=now,
            mutate=mutate,
            stage={
                "kind": "ROUTED",
                "label": "AstrBot 已路由至 Groupmate",
                "at": int(now),
                "status": "DONE",
            },
        )

    def record_evaluation(self, evaluation: object, now: int) -> None:
        event = getattr(evaluation, "source_event", None)
        if not isinstance(event, SocialEventEnvelope):
            return
        frame = getattr(evaluation, "frame", None)
        result = getattr(evaluation, "governor_result", None)
        if result is None:
            return
        outcome = str(getattr(result, "outcome", "PENDING")).upper()
        trigger = str(getattr(frame, "trigger_kind", "") or "")
        mode = self._mode_value(getattr(evaluation, "runtime_mode", "OFF"))
        reason_codes = tuple(getattr(result, "reason_codes", ()) or ())
        reasons = [self._reason_label(item) for item in reason_codes]
        diagnostics = [
            {
                "worker": str(getattr(item, "worker", "unknown")),
                "status": str(getattr(item, "status", "FAILED")),
                "latency_ms": max(0, int(getattr(item, "latency_ms", 0) or 0)),
                "diagnostic_code": (
                    str(getattr(item, "diagnostic_code"))
                    if getattr(item, "diagnostic_code", None)
                    else None
                ),
                "queue_wait_ms": max(
                    0, int(getattr(item, "queue_wait_ms", 0) or 0)
                ),
                "provider_latency_ms": max(
                    0, int(getattr(item, "provider_latency_ms", 0) or 0)
                ),
                "input_bytes": max(
                    0, int(getattr(item, "input_bytes", 0) or 0)
                ),
                "timeout_ms": max(
                    0, int(getattr(item, "timeout_ms", 0) or 0)
                ),
                "backend": str(getattr(item, "backend", "") or ""),
                "model": str(getattr(item, "model", "") or ""),
            }
            for item in tuple(getattr(evaluation, "cognition_diagnostics", ()) or ())
        ]
        candidate_response = str(
            getattr(evaluation, "candidate_response", "") or ""
        ).strip()
        reply_diagnostic = str(
            getattr(evaluation, "reply_diagnostic", "") or ""
        ).strip()
        lane = str(
            getattr(evaluation, "participation_lane", "AMBIENT")
            or "AMBIENT"
        ).upper()
        if lane not in {"DIRECT_FAST", "CONTINUATION", "AMBIENT"}:
            lane = "AMBIENT"
        candidates = tuple(getattr(evaluation, "candidates", ()) or ())
        candidate_source = (
            "deterministic"
            if candidates and lane in {"DIRECT_FAST", "CONTINUATION"}
            else "model"
            if candidates
            else "none"
        )
        participation_diagnostics = [
            self._safe_text(str(item), 80)
            for item in tuple(
                getattr(evaluation, "participation_diagnostics", ()) or ()
            )
        ]
        judgement = self._judgement(
            evaluation=evaluation,
            lane=lane,
            outcome=outcome,
            diagnostics=diagnostics,
        )

        def mutate(summary: dict[str, object]) -> None:
            summary["understanding"] = {
                "status": (
                    "DEGRADED"
                    if any(item["status"] != "SUCCEEDED" for item in diagnostics)
                    else "READY"
                ),
                "summary": _TRIGGER_LABELS.get(trigger, "已结合当前群聊上下文完成理解"),
                "diagnostics": diagnostics,
                "candidate_count": len(candidates),
                "candidate_source": candidate_source,
                "participation_diagnostics": participation_diagnostics,
            }
            summary["judgement"] = judgement
            summary["decision"] = {
                "outcome": outcome,
                "pre_gate_outcome": outcome,
                "participation_lane": lane,
                "would_reply": outcome == "ACT",
                "label": _OUTCOME_LABELS.get(outcome, "已完成判断"),
                "reasons": reasons,
                "candidate_response": candidate_response or None,
                "reply_diagnostic": reply_diagnostic or None,
            }
            if outcome == "SILENCE":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "SILENT",
                    "label": "本次不回复",
                }
            elif mode == "SHADOW" and outcome == "ACT":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "BLOCKED_BY_SHADOW",
                    "label": "SHADOW：已完成判断，未发送",
                }
            elif outcome == "OBSERVE":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "OBSERVED",
                    "label": "继续观察",
                }
            elif outcome == "DEFER":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "DEFERRED",
                    "label": "等待重新判断",
                }
            elif outcome == "ACT":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "PLANNING",
                    "label": "正在准备回复",
                }

        stages = []
        if frame is not None:
            stages.extend(
                (
                    {
                        "kind": "ATTENDED",
                        "label": _TRIGGER_LABELS.get(trigger, "进入注意判断"),
                        "at": int(now),
                        "status": "DONE",
                    },
                    {
                        "kind": "UNDERSTOOD",
                        "label": "已理解当前群聊场景",
                        "at": int(now),
                        "status": "DONE",
                    },
                )
            )
        stages.append(
            {
                "kind": "DECIDED",
                "label": _OUTCOME_LABELS.get(outcome, "已完成判断"),
                "at": int(now),
                "status": "DONE",
            }
        )
        self._mutate(event.event_id, now, mutate, stages=tuple(stages))

    def _judgement(
        self,
        *,
        evaluation: object,
        lane: str,
        outcome: str,
        diagnostics: list[dict[str, object]],
    ) -> dict[str, object]:
        would_reply = outcome == "ACT"
        if lane != "AMBIENT":
            judgement = {
                "source": "policy",
                "status": "not_required",
                "decision": "speak" if would_reply else "silence",
                "would_reply": would_reply,
                "label": _OUTCOME_LABELS.get(outcome, "已完成判断"),
            }
            source_event = getattr(evaluation, "source_event", None)
            if isinstance(source_event, SocialEventEnvelope):
                evidence = self._address_evidence(source_event)
                if evidence:
                    judgement.update(evidence)
                    judgement["reason"] = evidence["address_reason"]
            return judgement
        observations = tuple(
            getattr(evaluation, "cognitive_observations", ()) or ()
        )
        model_unavailable = any(
            str(item.get("worker") or "") == "ambient_social_assessor"
            and str(item.get("status") or "") != "SUCCEEDED"
            for item in diagnostics
        )
        assessment = next(
            (
                item
                for item in reversed(observations)
                if str(getattr(item, "kind", ""))
                == "participation_assessment"
            ),
            None,
        )
        if model_unavailable or assessment is None:
            return {
                "source": "model",
                "status": "unavailable",
                "decision": None,
                "would_reply": would_reply,
                "label": "模型判断未采用",
            }
        proposition = getattr(assessment, "proposition", {})
        if not isinstance(proposition, Mapping):
            proposition = {}
        decision = str(proposition.get("decision") or "").lower()
        judgement = {
            "source": "model",
            "status": "accepted",
            "decision": decision if decision in {"speak", "silence"} else None,
            "would_reply": would_reply,
            "label": _OUTCOME_LABELS.get(outcome, "已完成判断"),
        }
        reason = self._safe_text(proposition.get("reason"), 80)
        if reason:
            judgement["reason"] = reason
        evidence = self._evidence_preview(
            evaluation,
            tuple(getattr(assessment, "evidence_event_ids", ()) or ()),
        )
        if evidence:
            judgement["evidence"] = evidence
        return judgement

    def _evidence_preview(
        self, evaluation: object, evidence_event_ids: tuple[object, ...]
    ) -> dict[str, object] | None:
        source_event = getattr(evaluation, "source_event", None)
        if not isinstance(source_event, SocialEventEnvelope):
            return None
        persona_id = source_event.persona_id
        group_id = source_event.group_id or ""
        if not group_id:
            return None
        with connect_database(self.path) as db:
            for event_id in reversed(evidence_event_ids):
                row = db.execute(
                    "SELECT state_json FROM message_traces "
                    "WHERE event_id=? AND persona_id=? AND group_id=?",
                    (str(event_id), persona_id, group_id),
                ).fetchone()
                if row is None:
                    continue
                summary = json.loads(str(row["state_json"]))
                actor = summary.get("actor")
                message = summary.get("message")
                if isinstance(actor, Mapping) and isinstance(message, Mapping):
                    return {"actor": dict(actor), "message": dict(message)}
        return None

    def record_plan(self, event_id: str, plan: object, now: int) -> None:
        def mutate(summary: dict[str, object]) -> None:
            delivery = dict(summary.get("delivery") or {})
            if delivery.get("mode") == "SHADOW":
                delivery.update(
                    status="BLOCKED_BY_SHADOW",
                    label="SHADOW：已生成方案但不会发送",
                )
            else:
                delivery.update(status="READY", label="回复已准备，等待发送")
            summary["delivery"] = delivery
            expression = getattr(plan, "expression", None)
            if expression is not None:
                summary["expression"] = {
                    "reaction_stance": self._safe_text(
                        getattr(expression, "reaction_stance", ""), 40
                    ),
                    "core_response_goal": self._safe_text(
                        getattr(expression, "core_response_goal", ""), 80
                    ),
                    "followup_hook": self._safe_text(
                        getattr(expression, "followup_hook", ""), 40
                    ),
                    "message_count": max(
                        1,
                        min(2, int(getattr(expression, "message_count", 1))),
                    ),
                    "capability_request": self._safe_text(
                        getattr(expression, "capability_request", ""), 60
                    )
                    or None,
                }

        self._mutate(
            event_id,
            now,
            mutate,
            stage={
                "kind": "PLANNED",
                "label": "回复方案已生成",
                "at": int(now),
                "status": "DONE",
            },
        )

    def record_delivery(
        self,
        correlation_id: str,
        status: str,
        platform_message_id: str | None,
        error_code: str | None,
        now: int,
    ) -> None:
        normalized = str(status or "UNKNOWN").upper()
        labels = {
            "SENT": "消息已发送",
            "FAILED": "发送失败",
            "UNKNOWN": "发送结果未知",
        }

        def mutate(summary: dict[str, object]) -> None:
            delivery = dict(summary.get("delivery") or {})
            delivery.update(status=normalized, label=labels.get(normalized, "发送状态已更新"))
            if error_code:
                delivery["error"] = self._safe_text(error_code, 80)
            summary["delivery"] = delivery

        event_id = self._event_id_for_correlation(correlation_id)
        if event_id:
            self._mutate(
                event_id,
                now,
                mutate,
                stage={
                    "kind": "DELIVERED",
                    "label": labels.get(normalized, "发送状态已更新"),
                    "at": int(now),
                    "status": "DONE" if normalized == "SENT" else normalized,
                },
            )

    def query(self, *, persona_id: str, group_id: str) -> dict[str, object]:
        persona, group = self._scope(persona_id, group_id)
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT entity_ref, revision, state_json, received_at, updated_at "
                "FROM message_traces WHERE persona_id=? AND group_id=? "
                "ORDER BY received_at DESC, entity_ref DESC LIMIT 200",
                (persona, group),
            ).fetchall()
            cursor_row = db.execute(
                "SELECT version, updated_at FROM projection_cursors "
                "WHERE projection_name='traces'"
            ).fetchone()
        version = int(cursor_row[0]) if cursor_row else 0
        as_of = int(cursor_row[1]) if cursor_row else None
        return {
            "projection": "traces",
            "as_of": as_of,
            "cursor": version,
            "projection_version": version,
            "stale": False,
            "items": [self._public_item(row) for row in rows],
        }

    def detail(
        self, *, persona_id: str, group_id: str, trace_ref: str
    ) -> dict[str, object]:
        persona, group = self._scope(persona_id, group_id)
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT entity_ref, revision, state_json, received_at, updated_at "
                "FROM message_traces WHERE persona_id=? AND group_id=? AND entity_ref=?",
                (persona, group, str(trace_ref)),
            ).fetchone()
        if row is None:
            raise KeyError("message trace not found")
        return self._public_item(row)

    def _create_or_update(
        self,
        event: SocialEventEnvelope,
        summary: dict[str, object],
        stage: dict[str, object],
        now: int,
    ) -> None:
        entity_ref = self._opaque_ref("traces", event.persona_id, event.group_id or "", event.event_id)
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state_json, revision, received_at FROM message_traces WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if row is not None:
                db.commit()
                return
            revision = 1
            received_at = int(now)
            db.execute(
                "INSERT INTO message_traces(event_id, correlation_id, entity_ref, persona_id, "
                "group_id, state_json, revision, received_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.correlation_id,
                    entity_ref,
                    event.persona_id,
                    event.group_id,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    revision,
                    received_at,
                    int(now),
                ),
            )
            self._upsert_stage(db, event.event_id, stage)
            summary["stages"] = self._load_stages(db, event.event_id)
            self._update_timing(summary, received_at, now)
            db.execute(
                "UPDATE message_traces SET state_json=? WHERE event_id=?",
                (json.dumps(summary, ensure_ascii=False, sort_keys=True), event.event_id),
            )
            self._publish(db, event.event_id, entity_ref, event.persona_id, event.group_id, summary, revision, now)
            db.commit()

    def _mutate(
        self,
        event_id: str,
        now: int,
        mutate,
        stage: dict[str, object] | None = None,
        stages: tuple[dict[str, object], ...] = (),
    ) -> None:
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT entity_ref, persona_id, group_id, state_json, revision, received_at "
                "FROM message_traces WHERE event_id=?",
                (str(event_id),),
            ).fetchone()
            if row is None:
                db.rollback()
                return
            summary = json.loads(str(row["state_json"]))
            mutate(summary)
            for item in ((stage,) if stage is not None else stages):
                self._upsert_stage(db, str(event_id), item)
            summary["stages"] = self._load_stages(db, str(event_id))
            self._update_timing(summary, int(row["received_at"]), now)
            revision = int(row["revision"]) + 1
            db.execute(
                "UPDATE message_traces SET state_json=?, revision=?, updated_at=? WHERE event_id=?",
                (json.dumps(summary, ensure_ascii=False, sort_keys=True), revision, int(now), str(event_id)),
            )
            self._publish(
                db,
                str(event_id),
                str(row["entity_ref"]),
                str(row["persona_id"]),
                str(row["group_id"]),
                summary,
                revision,
                now,
            )
            db.commit()

    def _publish(
        self,
        db,
        event_id: str,
        entity_ref: str,
        persona_id: str,
        group_id: str | None,
        summary: dict[str, object],
        revision: int,
        now: int,
    ) -> None:
        encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        db.execute(
            "INSERT INTO control_projection_items(projection_name, entity_key, entity_ref, "
            "persona_id, group_id, kind, projection_version, summary_json, evidence_refs_json, as_of) "
            "VALUES('traces', ?, ?, ?, ?, 'message.trace', ?, ?, '[]', ?) "
            "ON CONFLICT(projection_name, entity_key) DO UPDATE SET "
            "projection_version=excluded.projection_version, summary_json=excluded.summary_json, "
            "as_of=excluded.as_of",
            (event_id, entity_ref, persona_id, group_id, int(revision), encoded, int(now)),
        )
        effect_id = f"message-trace:{hashlib.sha256(event_id.encode()).hexdigest()[:20]}:{revision}"
        journal_head = int(db.execute("SELECT COALESCE(MAX(rowid), 0) FROM journal").fetchone()[0])
        db.execute(
            "INSERT OR IGNORE INTO control_projection_events(projection_name, source_effect_id, "
            "source_journal_rowid, persona_id, group_id, kind, entity_ref, projection_version, "
            "summary_json, created_at) VALUES('traces', ?, ?, ?, ?, 'message.trace', ?, ?, ?, ?)",
            (effect_id, journal_head, persona_id, group_id, entity_ref, int(revision), encoded, int(now)),
        )
        db.execute(
            "INSERT INTO projection_cursors(projection_name, last_journal_rowid, version, updated_at) "
            "VALUES('traces', ?, ?, ?) ON CONFLICT(projection_name) DO UPDATE SET "
            "last_journal_rowid=excluded.last_journal_rowid, version=MAX(version, excluded.version), "
            "updated_at=excluded.updated_at",
            (journal_head, int(revision), int(now)),
        )

    def _ensure_tables(self) -> None:
        with connect_database(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS message_traces (
                    event_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    entity_ref TEXT NOT NULL UNIQUE,
                    persona_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_message_traces_scope
                    ON message_traces(persona_id, group_id, received_at DESC);
                CREATE TABLE IF NOT EXISTS message_trace_stages (
                    event_id TEXT NOT NULL REFERENCES message_traces(event_id),
                    stage_kind TEXT NOT NULL,
                    stage_order INTEGER NOT NULL,
                    stage_json TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    PRIMARY KEY(event_id, stage_kind)
                );
                CREATE TABLE IF NOT EXISTS control_projection_items (
                    projection_name TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    entity_ref TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    group_id TEXT,
                    kind TEXT NOT NULL,
                    projection_version INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    as_of INTEGER NOT NULL,
                    PRIMARY KEY(projection_name, entity_key)
                );
                CREATE TABLE IF NOT EXISTS control_projection_events (
                    stream_cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    projection_name TEXT NOT NULL,
                    source_effect_id TEXT NOT NULL,
                    source_journal_rowid INTEGER NOT NULL,
                    persona_id TEXT NOT NULL,
                    group_id TEXT,
                    kind TEXT NOT NULL,
                    entity_ref TEXT NOT NULL,
                    projection_version INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(projection_name, source_effect_id)
                );
                """
            )

    @staticmethod
    def _upsert_stage(db, event_id: str, stage: Mapping[str, object]) -> None:
        kind = str(stage.get("kind") or "").upper()
        if kind not in _STAGE_ORDER:
            return
        occurred_at = int(stage.get("at") or 0)
        db.execute(
            "INSERT INTO message_trace_stages(event_id, stage_kind, stage_order, stage_json, occurred_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(event_id, stage_kind) DO UPDATE SET "
            "stage_json=excluded.stage_json, occurred_at=excluded.occurred_at",
            (
                event_id,
                kind,
                _STAGE_ORDER[kind],
                json.dumps(dict(stage), ensure_ascii=False, sort_keys=True),
                occurred_at,
            ),
        )

    @staticmethod
    def _load_stages(db, event_id: str) -> list[dict[str, object]]:
        return [
            json.loads(str(row[0]))
            for row in db.execute(
                "SELECT stage_json FROM message_trace_stages WHERE event_id=? "
                "ORDER BY stage_order, occurred_at",
                (event_id,),
            ).fetchall()
        ]

    def _event_id_for_correlation(self, correlation_id: str) -> str | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT event_id FROM message_traces WHERE correlation_id=? ORDER BY received_at DESC LIMIT 1",
                (str(correlation_id),),
            ).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _update_timing(summary: dict[str, object], received_at: int, now: int) -> None:
        summary["timing"] = {
            "received_at": int(received_at),
            "updated_at": int(now),
            "total_ms": max(0, int(now) - int(received_at)) * 1000,
        }

    @staticmethod
    def _public_item(row) -> dict[str, object]:
        return {
            "entity_ref": str(row["entity_ref"]),
            "kind": "message.trace",
            "projection_version": int(row["revision"]),
            "summary": json.loads(str(row["state_json"])),
            "evidence_refs": [],
            "as_of": int(row["updated_at"]),
        }

    @staticmethod
    def _opaque_ref(prefix: str, *parts: str) -> str:
        value = "\0".join(str(part) for part in parts)
        return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:20]}"

    @staticmethod
    def _safe_text(value: object, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    @classmethod
    def _address_evidence(
        cls, event: SocialEventEnvelope
    ) -> dict[str, object]:
        kind = cls._safe_text(event.payload.get("address_kind"), 24).upper()
        addressed = bool(event.payload.get("addressed_to_bot"))
        if not addressed or kind not in _ADDRESS_KINDS:
            return {}
        alias = cls._safe_text(event.payload.get("matched_alias"), 24)
        candidate = cls._safe_text(event.payload.get("alias_candidate"), 24)
        return {
            "address_kind": kind,
            "matched_alias": alias or None,
            "addressed_to_bot": True,
            "alias_candidate": candidate or None,
            "address_reason": _direct_reason(event),
        }

    @classmethod
    def _message_summary(cls, value: object) -> str:
        text = cls._safe_text(value, 500)
        text = _URL_RE.sub("[链接]", text)
        text = _LONG_NUMBER_RE.sub("[号码]", text)
        return text[:240] or "[非文本消息]"

    @staticmethod
    def _media_types(value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        result = []
        for item in value:
            kind = str(item.get("type") if isinstance(item, Mapping) else item or "").lower()
            if kind and kind not in result:
                result.append(kind[:24])
        return result[:8]

    @staticmethod
    def _mode_value(value: object) -> str:
        raw = getattr(value, "value", value)
        normalized = str(raw or "OFF").upper()
        return normalized if normalized in {"OFF", "SHADOW", "SOCIAL_RUNTIME"} else "OFF"

    @staticmethod
    def _reason_label(code: object) -> str:
        normalized = str(code or "").strip()
        return _REASON_LABELS.get(normalized, normalized.replace("_", " ")[:80])

    @staticmethod
    def _scope(persona_id: str, group_id: str) -> tuple[str, str]:
        persona = str(persona_id).strip()
        group = str(group_id).strip()
        if not persona or not group:
            raise ValueError("message trace query requires persona and group scope")
        return persona, group


__all__ = ("MessageTraceRepository",)
