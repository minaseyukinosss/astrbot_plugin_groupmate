"""SQLite persistence for member style assets and group imitation sessions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from ..persistence.schema import connect_database, initialize_database
from .contracts import ProfileObservation
from .speech_style import (
    ImitationSession,
    MemberSpeechStyle,
    MemberStyleEvidence,
    MemberStyleEvidencePolicy,
    MemberStyleSetting,
)


class MemberStyleRepository:
    MAX_SESSION_SECONDS = 3 * 24 * 60 * 60

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        initialize_database(self.path)

    def setting(self, group_id: str, member_id: str) -> MemberStyleSetting:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT * FROM member_style_settings WHERE group_id=? AND member_id=?",
                (str(group_id), str(member_id)),
            ).fetchone()
        if row is None:
            return MemberStyleSetting.disabled(str(group_id), str(member_id))
        return self._setting(row)

    def set_enabled(
        self,
        group_id: str,
        member_id: str,
        *,
        enabled: bool,
        updated_by: str,
        now: int,
    ) -> MemberStyleSetting:
        normalized_group = str(group_id).strip()
        normalized_member = str(member_id).strip()
        actor = str(updated_by).strip()
        decision_now = max(0, int(now))
        if not normalized_group or not normalized_member or not actor:
            raise ValueError("style setting scope and administrator are required")
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM member_style_settings WHERE group_id=? AND member_id=?",
                (normalized_group, normalized_member),
            ).fetchone()
            version = (int(row["version"]) if row is not None else 0) + 1
            windows = (
                list(json.loads(str(row["collection_windows_json"])))
                if row is not None
                else []
            )
            was_enabled = bool(row["enabled"]) if row is not None else False
            if enabled and not was_enabled:
                windows.append([decision_now, None])
            elif not enabled and was_enabled and windows:
                windows[-1][1] = decision_now
            enabled_at = (
                int(row["enabled_at"])
                if row is not None and int(row["enabled_at"]) > 0
                else decision_now if enabled else 0
            )
            db.execute(
                "INSERT INTO member_style_settings(group_id,member_id,enabled,enabled_at,"
                "updated_by,updated_at,version,collection_windows_json) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(group_id,member_id) DO UPDATE SET enabled=excluded.enabled,"
                "enabled_at=excluded.enabled_at,updated_by=excluded.updated_by,"
                "updated_at=excluded.updated_at,version=excluded.version,"
                "collection_windows_json=excluded.collection_windows_json",
                (
                    normalized_group,
                    normalized_member,
                    int(bool(enabled)),
                    enabled_at,
                    actor,
                    decision_now,
                    version,
                    self._json(windows),
                ),
            )
            if not enabled:
                db.execute(
                    "UPDATE imitation_sessions SET stopped_at=?,stopped_by=?,"
                    "stop_reason='distillation_disabled' WHERE group_id=? "
                    "AND target_member_id=? AND stopped_at IS NULL AND expires_at>?",
                    (decision_now, actor, normalized_group, normalized_member, decision_now),
                )
            updated = db.execute(
                "SELECT * FROM member_style_settings WHERE group_id=? AND member_id=?",
                (normalized_group, normalized_member),
            ).fetchone()
        return self._setting(updated)

    def enabled_members(self) -> tuple[MemberStyleSetting, ...]:
        with connect_database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM member_style_settings WHERE enabled=1 "
                "ORDER BY group_id,member_id"
            ).fetchall()
        return tuple(self._setting(row) for row in rows)

    def eligible_observations(
        self,
        group_id: str,
        member_id: str,
        *,
        limit: int = 500,
        policy: MemberStyleEvidencePolicy | None = None,
    ) -> tuple[MemberStyleEvidence, ...]:
        setting = self.setting(group_id, member_id)
        if not setting.enabled and setting.version == 0:
            return ()
        with connect_database(self.path) as db:
            setting_row = db.execute(
                "SELECT collection_windows_json FROM member_style_settings "
                "WHERE group_id=? AND member_id=?",
                (str(group_id), str(member_id)),
            ).fetchone()
            rows = db.execute(
                "SELECT * FROM profile_observations WHERE group_id=? AND actor_id=? "
                "ORDER BY occurred_at,event_id LIMIT ?",
                (str(group_id), str(member_id), max(1, min(2000, int(limit)))),
            ).fetchall()
        windows = tuple(json.loads(str(setting_row[0]))) if setting_row else ()
        selector = policy or MemberStyleEvidencePolicy()
        result = []
        for row in rows:
            occurred_at = int(row["occurred_at"])
            if not any(
                occurred_at >= int(start)
                and (end is None or occurred_at < int(end))
                for start, end in windows
            ):
                continue
            observation = ProfileObservation(
                event_id=str(row["event_id"]),
                persona_id=str(row["persona_id"]),
                group_id=str(row["group_id"]),
                actor_id=str(row["actor_id"]),
                payload=dict(json.loads(str(row["observation_json"]))),
                occurred_at=occurred_at,
                status=str(row["status"]),
                attempt=int(row["attempt"]),
                next_attempt_at=int(row["next_attempt_at"]),
                diagnostic_code=(
                    str(row["diagnostic_code"])
                    if row["diagnostic_code"] is not None
                    else None
                ),
            )
            evidence = selector.select(observation)
            if evidence is not None:
                result.append(evidence)
        return tuple(result)

    def publish(self, style: MemberSpeechStyle) -> MemberSpeechStyle:
        setting = self.setting(style.group_id, style.member_id)
        if not setting.enabled:
            raise ValueError("member style distillation is disabled")
        with connect_database(self.path) as db:
            current = db.execute(
                "SELECT MAX(version) FROM member_speech_style_versions "
                "WHERE group_id=? AND member_id=?",
                (style.group_id, style.member_id),
            ).fetchone()[0]
            expected = int(current or 0) + 1
            if style.version != expected:
                raise ValueError("member style version is not next")
            db.execute(
                "INSERT INTO member_speech_style_versions(group_id,member_id,version,"
                "status,style_json,eligible_message_count,active_day_count,"
                "scene_types_json,evidence_event_ids_json,generated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    style.group_id,
                    style.member_id,
                    style.version,
                    style.status,
                    self._json(asdict(style)),
                    style.eligible_message_count,
                    style.active_day_count,
                    self._json(style.scene_types),
                    self._json(style.evidence_event_ids),
                    style.generated_at,
                ),
            )
        return style

    def style(
        self, group_id: str, member_id: str, version: int
    ) -> MemberSpeechStyle | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT style_json FROM member_speech_style_versions "
                "WHERE group_id=? AND member_id=? AND version=?",
                (str(group_id), str(member_id), int(version)),
            ).fetchone()
        return None if row is None else self._style(str(row[0]))

    def latest_ready(
        self, group_id: str, member_id: str
    ) -> MemberSpeechStyle | None:
        if not self.setting(group_id, member_id).enabled:
            return None
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT style_json FROM member_speech_style_versions "
                "WHERE group_id=? AND member_id=? AND status='READY' "
                "ORDER BY version DESC LIMIT 1",
                (str(group_id), str(member_id)),
            ).fetchone()
        return None if row is None else self._style(str(row[0]))

    def start_session(
        self,
        *,
        group_id: str,
        target_member_id: str,
        target_display_name: str,
        style_version: int,
        started_by: str,
        started_at: int,
        expires_at: int,
    ) -> ImitationSession:
        start = int(started_at)
        expiry = int(expires_at)
        if expiry <= start or expiry - start > self.MAX_SESSION_SECONDS:
            raise ValueError("imitation expiry must be within three days")
        style = self.style(group_id, target_member_id, style_version)
        if style is None or style.status != "READY" or not self.setting(group_id, target_member_id).enabled:
            raise ValueError("member style is not selectable")
        identity = "\0".join(
            (str(group_id), str(target_member_id), str(start), str(started_by))
        )
        session_id = "imitation:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        with connect_database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE imitation_sessions SET stopped_at=?,stopped_by=?,"
                "stop_reason='replaced' WHERE group_id=? AND stopped_at IS NULL "
                "AND expires_at>?",
                (start, str(started_by), str(group_id), start),
            )
            db.execute(
                "INSERT INTO imitation_sessions(session_id,group_id,target_member_id,"
                "target_display_name,style_version,started_by_admin_id,started_at,"
                "expires_at,stopped_at,stopped_by,stop_reason) "
                "VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (
                    session_id,
                    str(group_id),
                    str(target_member_id),
                    str(target_display_name),
                    int(style_version),
                    str(started_by),
                    start,
                    expiry,
                ),
            )
            row = db.execute(
                "SELECT * FROM imitation_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._session(row)

    def active_session(self, group_id: str, *, now: int) -> ImitationSession | None:
        with connect_database(self.path) as db:
            row = db.execute(
                "SELECT s.* FROM imitation_sessions s "
                "JOIN member_style_settings x ON x.group_id=s.group_id "
                "AND x.member_id=s.target_member_id AND x.enabled=1 "
                "JOIN member_speech_style_versions v ON v.group_id=s.group_id "
                "AND v.member_id=s.target_member_id AND v.version=s.style_version "
                "AND v.status='READY' WHERE s.group_id=? AND s.stopped_at IS NULL "
                "AND s.expires_at>? ORDER BY s.started_at DESC,s.session_id DESC LIMIT 1",
                (str(group_id), int(now)),
            ).fetchone()
        return None if row is None else self._session(row)

    def stop_session(
        self,
        group_id: str,
        *,
        stopped_by: str,
        now: int,
        requester_is_admin: bool,
    ) -> ImitationSession | None:
        current = self.active_session(group_id, now=now)
        if current is None:
            return None
        requester = str(stopped_by)
        if not requester_is_admin and requester != current.target_member_id:
            return None
        reason = "admin_stopped" if requester_is_admin else "target_opt_out"
        with connect_database(self.path) as db:
            changed = db.execute(
                "UPDATE imitation_sessions SET stopped_at=?,stopped_by=?,stop_reason=? "
                "WHERE session_id=? AND stopped_at IS NULL",
                (int(now), requester, reason, current.session_id),
            ).rowcount
            row = db.execute(
                "SELECT * FROM imitation_sessions WHERE session_id=?",
                (current.session_id,),
            ).fetchone()
        return self._session(row) if changed == 1 else None

    @staticmethod
    def _setting(row) -> MemberStyleSetting:
        return MemberStyleSetting(
            group_id=str(row["group_id"]),
            member_id=str(row["member_id"]),
            enabled=bool(row["enabled"]),
            enabled_at=int(row["enabled_at"]),
            updated_by=str(row["updated_by"]),
            updated_at=int(row["updated_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _style(encoded: str) -> MemberSpeechStyle:
        values = dict(json.loads(encoded))
        for name in (
            "opening_patterns", "progression_patterns", "closing_patterns",
            "stable_traits", "occasional_traits", "evidence_event_ids", "scene_types",
        ):
            values[name] = tuple(values.get(name) or ())
        return MemberSpeechStyle(**values)

    @staticmethod
    def _session(row) -> ImitationSession:
        return ImitationSession(
            session_id=str(row["session_id"]),
            group_id=str(row["group_id"]),
            target_member_id=str(row["target_member_id"]),
            target_display_name=str(row["target_display_name"]),
            style_version=int(row["style_version"]),
            started_by_admin_id=str(row["started_by_admin_id"]),
            started_at=int(row["started_at"]),
            expires_at=int(row["expires_at"]),
            stopped_at=(int(row["stopped_at"]) if row["stopped_at"] is not None else None),
            stopped_by=(str(row["stopped_by"]) if row["stopped_by"] is not None else None),
            stop_reason=(str(row["stop_reason"]) if row["stop_reason"] is not None else None),
        )

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ("MemberStyleRepository",)
