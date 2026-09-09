"""Framework-neutral control API plus the thin AstrBot Web API registrar."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from .participants import ParticipantDirectory
from .message_media import MessageMediaDirectory
from ..social_runtime.control.commands import (
    AdvanceRollout,
    ApproveCalibration,
    CancelTask,
    CommandContext,
    CommandError,
    CommandService,
    CommandValidationError,
    ConfirmKnowledgeConvention,
    CorrectProfileFact,
    CorrectSocialState,
    CreateConfigDraft,
    DryRunConfig,
    DisputeKnowledgeClaim,
    ExpectedVersionConflict,
    ForgetMemory,
    InvalidateKnowledgeCache,
    InvalidateProfileFact,
    LinkIdentity,
    MergeProfileIdentity,
    PauseRuntime,
    PublishConfig,
    RejectKnowledgeConvention,
    ResetState,
    RestoreConfig,
    RetryKnowledgeJob,
    ReviewEvidence,
    ReviewShadowDecision,
    SetMemberStyleDistillation,
    SetKnowledgeAmbientCanary,
    SetRuntimeMode,
    SplitProfileIdentity,
    SupersedeKnowledgeAlias,
    ValidateConfig,
)
from ..social_runtime.control.knowledge import KnowledgeControlQueries
from ..social_runtime.control.queries import ProjectionQueries
from ..social_runtime.control.stickers import StickerControlService
from ..social_runtime.control.stream import ProjectionStream
from ..social_runtime.contracts import SocialEventEnvelope
from ..social_runtime.stickers.cognition import InvalidStickerMeaning
from ..social_runtime.stickers.lexicon import InvalidStickerAsset, UnsafeStickerPath


@dataclass(frozen=True)
class WebRequest:
    method: str
    path: str
    query: Mapping[str, object]
    headers: Mapping[str, object]
    json_body: Mapping[str, object] | None
    username: str | None


@dataclass(frozen=True)
class WebResponse:
    status: int
    body: object
    headers: Mapping[str, str]


class ControlPlaneWebAPI:
    KNOWLEDGE_LIBRARY_QUERY_ENDPOINTS = (
        "knowledge/library/overview",
        "knowledge/library/entities",
        "knowledge/library/claims",
        "knowledge/library/entity-detail",
        "knowledge/library/jobs",
    )
    KNOWLEDGE_GROUP_QUERY_ENDPOINTS = (
        "knowledge/group/overview",
        "knowledge/group/aliases",
        "knowledge/group/conventions",
        "knowledge/group/entity-context",
        "knowledge/group/usage",
        "knowledge/group/jobs",
    )
    KNOWLEDGE_QUERY_ENDPOINTS = (
        KNOWLEDGE_LIBRARY_QUERY_ENDPOINTS + KNOWLEDGE_GROUP_QUERY_ENDPOINTS
    )
    QUERY_ENDPOINTS = (
        "bootstrap",
        "runtime",
        "traces",
        "activity",
        "scenes",
        "people",
        "culture",
        "tasks",
        "persona",
        "governance",
        "evaluation",
        "health",
        "profiles",
        "profile",
        "group-portrait",
    )
    STICKER_QUERY_ENDPOINTS = ("stickers", "stickers/detail")

    def __init__(
        self,
        *,
        queries: ProjectionQueries,
        knowledge_queries: KnowledgeControlQueries | None = None,
        sticker_queries: StickerControlService | None = None,
        stream: ProjectionStream,
        command_service_for: Callable[[str], CommandService],
        event_publisher: Callable[
            [SocialEventEnvelope], object | Awaitable[object]
        ],
        persona_id: str,
        group_ids: tuple[str, ...],
        admin_ids: tuple[str, ...],
        participants: ParticipantDirectory | None = None,
        message_media: MessageMediaDirectory | None = None,
        runtime_mode: str = "OFF",
        runtime_ready: bool | None = None,
        runtime_blockers: tuple[str, ...] = (),
        runtime_status_provider: Callable[[str], Mapping[str, object]]
        | None = None,
        persona_status_provider: Callable[[str], Mapping[str, object]]
        | None = None,
    ) -> None:
        self.queries = queries
        self.knowledge_queries = knowledge_queries
        self.sticker_queries = sticker_queries
        self.stream = stream
        self._command_service_for = command_service_for
        self._event_publisher = event_publisher
        self.persona_id = str(persona_id).strip()
        self._group_order = tuple(
            dict.fromkeys(str(value).strip() for value in group_ids if str(value).strip())
        )
        self.group_ids = frozenset(self._group_order)
        self.admin_ids = frozenset(
            str(value).strip() for value in admin_ids if str(value).strip()
        )
        self.participants = participants
        self.message_media = message_media
        self.runtime_mode = str(runtime_mode or "OFF").upper()
        self.runtime_ready = (
            self.runtime_mode != "OFF"
            if runtime_ready is None
            else bool(runtime_ready)
        )
        self.runtime_blockers = tuple(
            str(item).strip() for item in runtime_blockers if str(item).strip()
        )
        self._runtime_status_provider = runtime_status_provider
        self._persona_status_provider = persona_status_provider
        if not self.persona_id or not self.group_ids:
            raise ValueError("control API requires persona and group scope")
        self._degraded: dict[str, str] = {}

    def _live_runtime_status(self, group_id: str) -> dict[str, object]:
        empty_profile_status = {
            "enabled": False,
            "task_running": False,
            "pending_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_diagnostic": None,
        }
        empty_member_style_status = {
            "enabled": False,
            "task_running": False,
            "active_session": False,
            "session_expires_at": None,
            "last_diagnostic": None,
        }
        fallback = {
            "effective_runtime_mode": self.runtime_mode,
            "runtime_state": (
                "RUNNING"
                if self.runtime_ready and self.runtime_mode != "OFF"
                else "STOPPED"
            ),
            "runtime_ready": self.runtime_ready,
            "runtime_blockers": list(self.runtime_blockers),
            "profile_status": empty_profile_status,
            "member_style_status": empty_member_style_status,
        }
        provider = self._runtime_status_provider
        if provider is None:
            return fallback
        try:
            status = dict(provider(group_id))
        except Exception as exc:
            self._degraded["runtime_status"] = type(exc).__name__
            return {
                **fallback,
                "runtime_ready": False,
                "runtime_blockers": [
                    *fallback["runtime_blockers"],
                    "实时运行状态暂时不可用",
                ],
            }
        self._degraded.pop("runtime_status", None)
        raw_profile = status.get("profile_status")
        profile = (
            dict(raw_profile)
            if isinstance(raw_profile, Mapping)
            else empty_profile_status
        )
        raw_member_style = status.get("member_style_status")
        member_style = (
            dict(raw_member_style)
            if isinstance(raw_member_style, Mapping)
            else empty_member_style_status
        )
        return {
            "effective_runtime_mode": str(
                status.get("effective_runtime_mode") or self.runtime_mode
            ).upper(),
            "runtime_state": str(
                status.get("runtime_state") or "STOPPED"
            ).upper(),
            "runtime_ready": bool(status.get("runtime_ready")),
            "runtime_blockers": [
                str(item).strip()
                for item in status.get("runtime_blockers", ())
                if str(item).strip()
            ],
            "profile_status": {
                "enabled": bool(profile.get("enabled")),
                "task_running": bool(profile.get("task_running")),
                "pending_count": max(0, int(profile.get("pending_count") or 0)),
                "last_attempt_at": profile.get("last_attempt_at"),
                "last_success_at": profile.get("last_success_at"),
                "last_diagnostic": (
                    str(profile.get("last_diagnostic"))
                    if profile.get("last_diagnostic")
                    else None
                ),
            },
            "member_style_status": {
                "enabled": bool(member_style.get("enabled")),
                "task_running": bool(member_style.get("task_running")),
                "active_session": bool(member_style.get("active_session")),
                "session_expires_at": member_style.get("session_expires_at"),
                "last_diagnostic": (
                    str(member_style.get("last_diagnostic"))
                    if member_style.get("last_diagnostic")
                    else None
                ),
            },
        }

    def _resolved_persona_status(self, group_id: str) -> dict[str, object]:
        provider = self._persona_status_provider
        fallback = {
            "name": "Groupmate",
            "aliases": [],
            "preset": "",
            "preset_label": "当前人格资料",
        }
        if provider is None:
            return fallback
        try:
            status = dict(provider(group_id))
        except Exception as exc:
            self._degraded["persona_status"] = type(exc).__name__
            return fallback
        self._degraded.pop("persona_status", None)
        return {
            "name": str(status.get("name") or fallback["name"]),
            "aliases": [
                str(value)
                for value in status.get("aliases", ())
                if str(value).strip()
            ],
            "preset": str(status.get("preset") or ""),
            "preset_label": str(
                status.get("preset_label") or fallback["preset_label"]
            ),
        }

    def mark_degraded(self, component: str, reason: str) -> None:
        name = str(component).strip()
        detail = str(reason).strip()
        if name and detail:
            self._degraded[name] = detail

    def clear_degraded(self, component: str) -> None:
        self._degraded.pop(str(component).strip(), None)

    async def handle(self, request: WebRequest) -> WebResponse:
        endpoint = str(request.path).strip().strip("/")
        username = str(request.username or "").strip()
        if not username or (self.admin_ids and username not in self.admin_ids):
            return self._error(403, "administrator_forbidden")
        if endpoint in self.KNOWLEDGE_QUERY_ENDPOINTS:
            return self._knowledge_query(endpoint, request)
        if endpoint in {"knowledge/library/actions", "knowledge/group/actions"}:
            return await self._knowledge_action(
                request,
                scope_kind="library" if "/library/" in endpoint else "group",
            )
        if endpoint in self.STICKER_QUERY_ENDPOINTS or endpoint in {
            "stickers/preview",
            "stickers/actions",
        }:
            return await self._sticker_request(endpoint, request)
        if endpoint in self.QUERY_ENDPOINTS:
            if str(request.method).upper() != "GET":
                return self._error(405, "method_not_allowed")
            try:
                persona_id, group_id = self._scope(
                    request, allow_default=endpoint == "bootstrap"
                )
                if endpoint == "profile":
                    body = self.queries.profile(
                        persona_id=persona_id,
                        group_id=group_id,
                        member_ref=str(request.query.get("member_ref") or ""),
                    )
                elif endpoint == "group-portrait":
                    body = self.queries.group_portrait(
                        persona_id=persona_id,
                        group_id=group_id,
                    )
                elif endpoint == "traces":
                    raw_limit = request.query.get("limit", 100)
                    limit = min(200, max(1, int(raw_limit or 100)))
                    before = str(request.query.get("before") or "").strip()
                    body = self.queries.traces(
                        persona_id=persona_id,
                        group_id=group_id,
                        limit=limit,
                        before=before or None,
                    )
                else:
                    query = getattr(self.queries, endpoint)
                    body = query(persona_id=persona_id, group_id=group_id)
            except LookupError:
                return self._error(404, "scope_not_found")
            except ValueError:
                return self._error(400, "invalid_query")
            except Exception as exc:
                self._degraded["query"] = str(exc)
                return self._error(503, "projection_query_unavailable", detail=str(exc))
            self._degraded.pop("query", None)
            body = {
                **body,
                "scope": {"persona_id": persona_id, "group_id": group_id},
            }
            entity_ref = str(request.query.get("entity_ref") or "").strip()
            if entity_ref:
                items = [
                    item
                    for item in body.get("items", [])
                    if str(item.get("entity_ref") or "") == entity_ref
                ]
                if not items:
                    return self._error(404, "entity_not_found")
                body = {**body, "items": items}
            if endpoint == "bootstrap":
                runtime_status = self._live_runtime_status(group_id)
                persona_status = self._resolved_persona_status(group_id)
                body = {
                    **body,
                    "persona_id": persona_id,
                    "available_groups": list(self._group_order),
                    "selected_group_id": group_id,
                    "configured_runtime_mode": self.runtime_mode,
                    **runtime_status,
                    "resolved_persona": persona_status,
                }
            if endpoint == "health":
                live_status = self._live_runtime_status(group_id)
                body = {
                    **body,
                    "profile_status": live_status["profile_status"],
                    "member_style_status": live_status[
                        "member_style_status"
                    ],
                    "degraded": bool(self._degraded),
                    "degraded_reasons": list(self._degraded.values()),
                    "fallback_poll_seconds": 15,
                }
            return WebResponse(200, body, {"Content-Type": "application/json"})

        if endpoint == "avatar":
            if str(request.method).upper() != "GET":
                return self._error(405, "method_not_allowed")
            try:
                persona_id, group_id = self._scope(request)
                avatar_ref = str(request.query.get("avatar_ref") or "").strip()
                if (
                    self.participants is None
                    or not avatar_ref.startswith("participant:")
                    or not self.participants.contains(
                        avatar_ref,
                        persona_id=persona_id,
                        group_id=group_id,
                    )
                ):
                    raise LookupError("avatar not found")
                body = await self.participants.avatar_data(avatar_ref)
            except LookupError:
                return self._error(404, "avatar_not_found")
            except Exception as exc:
                return self._error(503, "avatar_unavailable", detail=str(exc))
            return WebResponse(200, body, {"Content-Type": "application/json"})

        if endpoint == "media":
            if str(request.method).upper() != "GET":
                return self._error(405, "method_not_allowed")
            try:
                persona_id, group_id = self._scope(request)
                media_ref = str(request.query.get("media_ref") or "").strip()
                if (
                    self.message_media is None
                    or not media_ref.startswith("media:")
                    or not self.message_media.contains(
                        media_ref,
                        persona_id=persona_id,
                        group_id=group_id,
                    )
                ):
                    raise LookupError("media not found")
                body = await self.message_media.media_data(media_ref)
            except LookupError:
                return self._error(404, "media_not_found")
            except (TypeError, ValueError) as exc:
                return self._error(415, "media_preview_rejected", detail=str(exc))
            except Exception as exc:
                return self._error(503, "media_unavailable", detail=str(exc))
            return WebResponse(200, body, {"Content-Type": "application/json"})

        if endpoint == "commands":
            if str(request.method).upper() != "POST":
                return self._error(405, "method_not_allowed")
            return await self._command(request)

        if endpoint == "events":
            if str(request.method).upper() != "GET":
                return self._error(405, "method_not_allowed")
            try:
                persona_id, group_id = self._scope(request)
                last_event_id = self._header(request.headers, "last-event-id")
                batch = self.stream.read(
                    last_event_id=last_event_id,
                    persona_id=persona_id,
                    group_id=group_id,
                    limit=100,
                )
                body = self.stream.encode(batch)
            except LookupError:
                return self._error(404, "scope_not_found")
            except Exception as exc:
                self._degraded["sse"] = str(exc)
                return WebResponse(
                    503,
                    {
                        "error": "projection_stream_unavailable",
                        "detail": str(exc),
                        "fallback_poll_seconds": 15,
                    },
                    {"Content-Type": "application/json"},
                )
            self._degraded.pop("sse", None)
            return WebResponse(
                200,
                body,
                {
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-store",
                },
            )

        return self._error(404, "endpoint_not_found")

    async def _sticker_request(self, endpoint: str, request: WebRequest) -> WebResponse:
        if self.sticker_queries is None:
            return self._error(503, "sticker_query_unavailable")
        try:
            self._scope(request, allow_default=True)
        except LookupError:
            return self._error(404, "scope_not_found")
        if endpoint == "stickers/actions":
            return await self._sticker_action(request)
        if str(request.method).upper() != "GET":
            return self._error(405, "method_not_allowed")
        try:
            if endpoint == "stickers":
                body = self.sticker_queries.overview()
            elif endpoint == "stickers/detail":
                body = self.sticker_queries.detail(
                    str(request.query.get("asset_id") or "")
                )
            else:
                body = self.sticker_queries.preview(
                    str(
                        request.query.get("asset_id")
                        or request.query.get("media_ref")
                        or ""
                    )
                )
        except LookupError:
            return self._error(404, "sticker_not_found")
        except (InvalidStickerAsset, UnsafeStickerPath) as exc:
            return self._error(400, "invalid_sticker", detail=str(exc))
        except Exception as exc:
            return self._error(503, "sticker_query_unavailable", detail=str(exc))
        return WebResponse(
            200,
            body,
            {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _sticker_action(self, request: WebRequest) -> WebResponse:
        if str(request.method).upper() != "POST":
            return self._error(405, "method_not_allowed")
        content_type = str(self._header(request.headers, "content-type") or "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            return self._error(415, "json_content_type_required")
        body = dict(request.json_body or {})
        try:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        except (TypeError, ValueError):
            return self._error(400, "invalid_command")
        limit = 2_800_000 if str(body.get("type") or "") == "sticker_upload" else 16_384
        if len(encoded) > limit:
            return self._error(413, "command_payload_too_large")
        try:
            result = self.sticker_queries.apply(body, now=int(time.time()))
        except LookupError:
            return self._error(404, "sticker_not_found")
        except (InvalidStickerAsset, InvalidStickerMeaning, ValueError) as exc:
            return self._error(400, "invalid_sticker", detail=str(exc))
        except Exception as exc:
            return self._error(503, "sticker_action_unavailable", detail=str(exc))
        return WebResponse(
            200,
            result,
            {
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _knowledge_query(
        self, endpoint: str, request: WebRequest
    ) -> WebResponse:
        if str(request.method).upper() != "GET":
            return self._knowledge_error(405, "method_not_allowed")
        if self.knowledge_queries is None:
            return self._knowledge_error(503, "knowledge_query_unavailable")
        try:
            if endpoint in self.KNOWLEDGE_LIBRARY_QUERY_ENDPOINTS:
                name = endpoint.removeprefix("knowledge/library/")
                if name == "overview":
                    body = self.knowledge_queries.library_overview(
                        now=int(time.time())
                    )
                elif name == "entity-detail":
                    body = self.knowledge_queries.library_entity_detail(
                        str(request.query.get("entity_id") or "")
                    )
                else:
                    cursor = self._optional_text(request.query.get("cursor"))
                    filters = self._knowledge_filters(request)
                    body = getattr(self.knowledge_queries, f"library_{name}")(
                        cursor, filters
                    )
                response_scope = {"kind": "library"}
            else:
                group_id = str(request.query.get("group_id") or "").strip()
                if not group_id:
                    return self._knowledge_error(400, "group_id_required")
                _, group_id = self._scope_values(
                    request.query.get("persona_id") or self.persona_id,
                    group_id,
                )
                name = endpoint.removeprefix("knowledge/group/")
                if name == "overview":
                    body = self.knowledge_queries.group_overview(
                        group_id, now=int(time.time())
                    )
                elif name == "entity-context":
                    body = self.knowledge_queries.group_entity_context(
                        group_id,
                        str(request.query.get("entity_id") or ""),
                    )
                else:
                    cursor = self._optional_text(request.query.get("cursor"))
                    filters = self._knowledge_filters(request)
                    method = (
                        self.knowledge_queries.conventions
                        if name == "conventions"
                        else getattr(self.knowledge_queries, f"group_{name}")
                    )
                    body = method(group_id, cursor, filters)
                response_scope = {"kind": "group", "group_id": group_id}
        except LookupError:
            return self._knowledge_error(404, "scope_not_found")
        except (TypeError, ValueError):
            return self._knowledge_error(400, "invalid_query")
        except Exception:
            return self._knowledge_error(503, "knowledge_query_unavailable")
        return WebResponse(
            200,
            {
                **body,
                "scope": response_scope,
            },
            self._knowledge_headers(),
        )

    async def _knowledge_action(
        self, request: WebRequest, *, scope_kind: str
    ) -> WebResponse:
        if str(request.method).upper() != "POST":
            return self._knowledge_error(405, "method_not_allowed")
        content_type = str(self._header(request.headers, "content-type") or "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            return self._knowledge_error(415, "json_content_type_required")
        try:
            encoded = json.dumps(
                request.json_body or {},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return self._knowledge_error(400, "invalid_command")
        if len(encoded) > 16_384:
            return self._knowledge_error(413, "command_payload_too_large")
        body = dict(request.json_body or {})
        library_actions = {
            "knowledge_claim_dispute",
            "knowledge_job_retry",
            "knowledge_cache_invalidate",
        }
        group_actions = {
            "knowledge_convention_confirm",
            "knowledge_convention_reject",
            "knowledge_alias_supersede",
            "knowledge_job_retry",
            "knowledge_ambient_canary_set",
        }
        allowed = library_actions if scope_kind == "library" else group_actions
        if str(body.get("type") or "") not in allowed:
            return self._knowledge_error(400, "unsupported_knowledge_action")
        body.setdefault("persona_id", self.persona_id)
        delegated = WebRequest(
            method=request.method,
            path="/commands",
            query=request.query,
            headers=request.headers,
            json_body=body,
            username=request.username,
        )
        response = await self._command(delegated)
        return WebResponse(response.status, response.body, self._knowledge_headers())

    @staticmethod
    def _knowledge_filters(request: WebRequest) -> dict[str, object]:
        return {
            key: request.query.get(key)
            for key in (
                "status",
                "entity_type",
                "claim_kind",
                "entity_id",
                "job_kind",
            )
            if request.query.get(key) is not None
        }

    async def _command(self, request: WebRequest) -> WebResponse:
        username = str(request.username or "").strip()
        if not username:
            return self._error(403, "administrator_identity_required")
        try:
            body = dict(request.json_body or {})
            persona_id, group_id = self._scope_values(
                request.query.get("persona_id") or body.get("persona_id"),
                request.query.get("group_id") or body.get("group_id"),
            )
            command = self._parse_command(body)
            context = CommandContext(
                admin_id=username,
                persona_id=persona_id,
                group_id=group_id,
                expected_version=self._integer(
                    body.get("expected_version"), "expected_version"
                ),
                reason=str(body.get("reason") or ""),
                confirmed=body.get("confirmed") is True,
            )
            result = self._command_service_for(username).execute(command, context)
            published = self._event_publisher(result.event)
            if inspect.isawaitable(published):
                await published
        except ExpectedVersionConflict as exc:
            return WebResponse(
                409,
                {
                    "error": "expected_version_conflict",
                    "expected_version": exc.expected_version,
                    "current_version": exc.current_version,
                },
                {"Content-Type": "application/json"},
            )
        except CommandError as exc:
            return self._error(exc.status_code, self._error_name(exc), detail=str(exc))
        except LookupError:
            return self._error(404, "scope_not_found")
        except (TypeError, ValueError) as exc:
            return self._error(400, "invalid_command", detail=str(exc))
        except Exception as exc:
            self._degraded["command_event"] = str(exc)
            return self._error(503, "command_event_unavailable", detail=str(exc))
        self._degraded.pop("command_event", None)
        return WebResponse(
            202,
            {
                "accepted": True,
                "command_id": result.command_id,
                "action_ref": result.action_id,
                "version": result.version,
            },
            {"Content-Type": "application/json"},
        )

    def _scope(
        self, request: WebRequest, *, allow_default: bool = False
    ) -> tuple[str, str]:
        persona_id = str(request.query.get("persona_id") or "").strip()
        group_id = str(request.query.get("group_id") or "").strip()
        if allow_default:
            persona_id = persona_id or self.persona_id
            group_id = group_id or self._group_order[0]
        return self._scope_values(persona_id, group_id)

    def _scope_values(
        self, persona_id: object, group_id: object
    ) -> tuple[str, str]:
        persona_id = str(persona_id or "").strip()
        group_id = str(group_id or "").strip()
        if persona_id != self.persona_id or group_id not in self.group_ids:
            raise LookupError("control API scope is not available")
        return persona_id, group_id

    @classmethod
    def _parse_command(cls, body: Mapping[str, object]):
        kind = str(body.get("type") or "").strip()
        command_id = cls._optional_text(body.get("command_id"))
        payload = body.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        constructors = {
            "runtime_mode_set": lambda: SetRuntimeMode(
                str(payload.get("runtime_mode") or ""),
                str(payload.get("readiness_report_hash") or ""),
                str(payload.get("old_instance_confirmation_token") or ""),
                command_id=command_id,
            ),
            "rollout_advance": lambda: AdvanceRollout(
                str(payload.get("readiness_report_hash") or ""),
                command_id=command_id,
            ),
            "pause": lambda: PauseRuntime(
                paused=cls._boolean(payload.get("paused"), "paused"),
                command_id=command_id,
            ),
            "reset": lambda: ResetState(
                str(payload.get("target") or ""), command_id=command_id
            ),
            "config_draft": lambda: CreateConfigDraft(
                str(payload.get("config_id") or ""),
                payload.get("config") if isinstance(payload.get("config"), Mapping) else {},
                command_id=command_id,
            ),
            "config_validate": lambda: ValidateConfig(
                str(payload.get("config_id") or ""), command_id=command_id
            ),
            "config_dry_run": lambda: DryRunConfig(
                str(payload.get("config_id") or ""),
                historical_events=tuple(payload.get("historical_events") or ()),
                worker_outputs=tuple(payload.get("worker_outputs") or ()),
                command_id=command_id,
            ),
            "config_publish": lambda: PublishConfig(
                str(payload.get("config_id") or ""), command_id=command_id
            ),
            "config_restore": lambda: RestoreConfig(
                str(payload.get("config_id") or ""),
                cls._integer(payload.get("source_version"), "source_version"),
                command_id=command_id,
            ),
            "review": lambda: ReviewEvidence(
                str(payload.get("entity_ref") or ""),
                str(payload.get("decision") or ""),
                command_id=command_id,
            ),
            "shadow_review": lambda: ReviewShadowDecision(
                str(payload.get("entity_ref") or ""),
                str(payload.get("decision") or ""),
                tuple(payload.get("categories") or ()),
                payload.get("correction")
                if isinstance(payload.get("correction"), Mapping)
                else None,
                command_id=command_id,
            ),
            "forget": lambda: ForgetMemory(
                str(payload.get("entity_ref") or ""), command_id=command_id
            ),
            "correct": lambda: CorrectSocialState(
                str(payload.get("entity_ref") or ""),
                payload.get("correction")
                if isinstance(payload.get("correction"), Mapping)
                else {},
                command_id=command_id,
            ),
            "link": lambda: LinkIdentity(
                str(payload.get("source_ref") or ""),
                str(payload.get("target_ref") or ""),
                tuple(payload.get("allowed_data_types") or ()),
                command_id=command_id,
            ),
            "cancel": lambda: CancelTask(
                str(payload.get("entity_ref") or ""), command_id=command_id
            ),
            "approve_calibration": lambda: ApproveCalibration(
                str(payload.get("entity_ref") or ""), command_id=command_id
            ),
            "profile_fact_correct": lambda: CorrectProfileFact(
                str(payload.get("member_ref") or ""),
                str(payload.get("fact_ref") or ""),
                str(payload.get("new_summary") or ""),
                command_id=command_id,
            ),
            "profile_fact_invalidate": lambda: InvalidateProfileFact(
                str(payload.get("member_ref") or ""),
                str(payload.get("fact_ref") or ""),
                command_id=command_id,
            ),
            "profile_identity_merge": lambda: MergeProfileIdentity(
                str(payload.get("source_member_ref") or ""),
                str(payload.get("target_member_ref") or ""),
                str(payload.get("stable_target_id") or ""),
                command_id=command_id,
            ),
            "profile_identity_split": lambda: SplitProfileIdentity(
                str(payload.get("source_member_ref") or ""),
                str(payload.get("new_stable_id") or ""),
                str(payload.get("new_display_name") or ""),
                tuple(payload.get("fact_refs") or ()),
                command_id=command_id,
            ),
            "member_style_distillation_set": lambda: SetMemberStyleDistillation(
                str(payload.get("member_ref") or ""),
                cls._boolean(payload.get("enabled"), "enabled"),
                command_id=command_id,
            ),
            "knowledge_convention_confirm": lambda: ConfirmKnowledgeConvention(
                str(payload.get("convention_id") or ""),
                command_id=command_id,
            ),
            "knowledge_convention_reject": lambda: RejectKnowledgeConvention(
                str(payload.get("convention_id") or ""),
                command_id=command_id,
            ),
            "knowledge_alias_supersede": lambda: SupersedeKnowledgeAlias(
                str(payload.get("alias_id") or ""),
                str(payload.get("replacement_entity_id") or ""),
                command_id=command_id,
            ),
            "knowledge_claim_dispute": lambda: DisputeKnowledgeClaim(
                str(payload.get("claim_id") or ""),
                command_id=command_id,
            ),
            "knowledge_job_retry": lambda: RetryKnowledgeJob(
                str(payload.get("job_id") or ""),
                command_id=command_id,
            ),
            "knowledge_cache_invalidate": lambda: InvalidateKnowledgeCache(
                str(payload.get("entity_id") or ""),
                command_id=command_id,
            ),
            "knowledge_ambient_canary_set": lambda: SetKnowledgeAmbientCanary(
                cls._boolean(payload.get("enabled"), "enabled"),
                command_id=command_id,
            ),
        }
        constructor = constructors.get(kind)
        if constructor is None:
            raise CommandValidationError("unsupported command type")
        return constructor()

    @staticmethod
    def _header(headers: Mapping[str, object], name: str) -> str | None:
        expected = name.casefold()
        for key, value in headers.items():
            if str(key).casefold() == expected:
                return str(value)
        return None

    @staticmethod
    def _integer(value: object, label: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{label} must be an integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if result < 0:
            raise ValueError(f"{label} must not be negative")
        return result

    @staticmethod
    def _boolean(value: object, label: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be a boolean")
        return value

    @staticmethod
    def _optional_text(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _error_name(error: CommandError) -> str:
        name = type(error).__name__
        result = []
        for char in name:
            if char.isupper() and result:
                result.append("_")
            result.append(char.casefold())
        return "".join(result)

    @staticmethod
    def _error(status: int, error: str, *, detail: str | None = None) -> WebResponse:
        body: dict[str, object] = {"error": error}
        if detail:
            body["detail"] = detail
        return WebResponse(status, body, {"Content-Type": "application/json"})

    @staticmethod
    def _knowledge_headers() -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }

    @classmethod
    def _knowledge_error(cls, status: int, error: str) -> WebResponse:
        return WebResponse(status, {"error": error}, cls._knowledge_headers())


class AstrBotControlPlaneRoutes:
    """Registers official AstrBot plugin routes without leaking its web framework."""

    PLUGIN_NAME = "astrbot_plugin_groupmate"
    ENDPOINTS = (
        ControlPlaneWebAPI.QUERY_ENDPOINTS
        + ControlPlaneWebAPI.KNOWLEDGE_QUERY_ENDPOINTS
        + ControlPlaneWebAPI.STICKER_QUERY_ENDPOINTS
        + (
            "knowledge/library/actions",
            "knowledge/group/actions",
            "stickers/preview",
            "stickers/actions",
            "avatar",
            "media",
            "commands",
            "events",
        )
    )

    def __init__(self, context: object, *, api_factory: Callable[[], object]) -> None:
        self.context = context
        self.api_factory = api_factory

    def register(self) -> None:
        registrar = getattr(self.context, "register_web_api", None)
        if not callable(registrar):
            raise RuntimeError("AstrBot Context does not support plugin Web APIs")
        for endpoint in self.ENDPOINTS:
            methods = (
                ["POST"]
                if endpoint in {
                    "commands",
                    "knowledge/library/actions",
                    "knowledge/group/actions",
                    "stickers/actions",
                }
                else ["GET"]
            )
            registrar(
                f"/{self.PLUGIN_NAME}/{endpoint}",
                self._handler(endpoint),
                methods,
                f"Groupmate control plane: {endpoint}",
            )

    def _handler(self, endpoint: str):
        async def handle():
            from astrbot.api.web import json_response, request, stream_response

            try:
                api = self.api_factory()
            except RuntimeError as exc:
                return json_response(
                    {"error": "control_plane_unavailable", "detail": str(exc)},
                    status_code=503,
                    headers=ControlPlaneWebAPI._knowledge_headers(),
                )
            body = (
                await request.json(default={})
                if str(request.method).upper() == "POST"
                else None
            )
            query = {
                "persona_id": request.query.get("persona_id"),
                "group_id": request.query.get("group_id"),
                "entity_ref": request.query.get("entity_ref"),
                "avatar_ref": request.query.get("avatar_ref"),
                "media_ref": request.query.get("media_ref"),
                "member_ref": request.query.get("member_ref"),
                "cursor": request.query.get("cursor"),
                "status": request.query.get("status"),
                "entity_type": request.query.get("entity_type"),
                "claim_kind": request.query.get("claim_kind"),
                "entity_id": request.query.get("entity_id"),
                "job_kind": request.query.get("job_kind"),
                "asset_id": request.query.get("asset_id"),
            }
            response = await api.handle(
                WebRequest(
                    method=str(request.method),
                    path=f"/{endpoint}",
                    query=query,
                    headers=dict(request.headers),
                    json_body=body,
                    username=request.username,
                )
            )
            if response.headers.get("Content-Type") == "text/event-stream":
                async def events():
                    try:
                        async for chunk in api.stream.subscribe(
                            last_event_id=ControlPlaneWebAPI._header(
                                dict(request.headers), "last-event-id"
                            ),
                            persona_id=str(query["persona_id"]),
                            group_id=str(query["group_id"]),
                        ):
                            api.clear_degraded("sse")
                            yield chunk
                    except Exception as exc:
                        api.mark_degraded("sse", str(exc))
                        return

                return stream_response(events())
            return json_response(
                response.body,
                status_code=response.status,
                headers=response.headers,
            )

        return handle


__all__ = (
    "AstrBotControlPlaneRoutes",
    "ControlPlaneWebAPI",
    "WebRequest",
    "WebResponse",
)
