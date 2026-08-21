"""Durable, privacy-trimmed message lifecycle traces for the plugin page."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from ...adapters.participants import ParticipantDirectory
from ..contracts import SocialEventEnvelope
from ..persistence.schema import connect_database, initialize_database


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_TRIGGER_LABELS = {
    "AMBIENT": "观察群聊上下文",
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
    "forced_observe": "SHADOW 模式禁止发送",
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


class MessageTraceRepository:
    """Maintains one public trace row for each incoming platform message."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)
        self.participants = ParticipantDirectory(
            self.path,
            self.path.parent / "avatars",
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
        delivery = (
            {
                "mode": mode,
                "status": "HANDED_OFF",
                "label": "结果由外部插件负责",
            }
            if external
            else {"mode": mode, "status": "RECEIVED", "label": "等待处理"}
        )
        summary = {
            "actor": {
                "member_ref": participant["member_ref"],
                "display_name": participant["display_name"],
                "avatar_ref": participant["avatar_ref"],
            },
            "message": {
                "summary": self._message_summary(event.payload.get("text")),
                "media_types": self._media_types(event.payload.get("media")),
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

    def mark_entered(self, event_id: str, now: int) -> None:
        def mutate(summary: dict[str, object]) -> None:
            summary["route"] = {
                "owner": "GROUPMATE",
                "label": "进入 Groupmate",
                "reason": "未被前置命令或外部插件截获",
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

        def mutate(summary: dict[str, object]) -> None:
            summary["understanding"] = {
                "status": "READY",
                "summary": _TRIGGER_LABELS.get(trigger, "已结合当前群聊上下文完成理解"),
            }
            summary["decision"] = {
                "outcome": outcome,
                "label": _OUTCOME_LABELS.get(outcome, "已完成判断"),
                "reasons": reasons,
            }
            if outcome == "SILENCE":
                summary["delivery"] = {
                    "mode": mode,
                    "status": "SILENT",
                    "label": "本次不回复",
                }
            elif outcome == "OBSERVE" or (mode == "SHADOW" and outcome == "ACT"):
                summary["delivery"] = {
                    "mode": mode,
                    "status": "OBSERVED",
                    "label": "SHADOW：仅观察，不发送" if mode == "SHADOW" else "继续观察",
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

    def record_plan(self, event_id: str, plan: object, now: int) -> None:
        def mutate(summary: dict[str, object]) -> None:
            delivery = dict(summary.get("delivery") or {})
            if delivery.get("mode") == "SHADOW":
                delivery.update(status="OBSERVED", label="SHADOW：已生成方案但不会发送")
            else:
                delivery.update(status="READY", label="回复已准备，等待发送")
            summary["delivery"] = delivery

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
