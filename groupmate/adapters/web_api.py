"""Framework-neutral control API plus the thin AstrBot Web API registrar."""

from __future__ import annotations

import inspect
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
    CorrectProfileFact,
    CorrectSocialState,
    CreateConfigDraft,
    DryRunConfig,
    ExpectedVersionConflict,
    ForgetMemory,
    InvalidateProfileFact,
    LinkIdentity,
    MergeProfileIdentity,
    PauseRuntime,
    PublishConfig,
    ResetState,
    RestoreConfig,
    ReviewEvidence,
    ReviewShadowDecision,
    SetMemberStyleDistillation,
    SetRuntimeMode,
    SplitProfileIdentity,
    ValidateConfig,
)
from ..social_runtime.control.queries import ProjectionQueries
from ..social_runtime.control.stream import ProjectionStream
from ..social_runtime.contracts import SocialEventEnvelope


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

    def __init__(
        self,
        *,
        queries: ProjectionQueries,
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
        fallback = {
            "effective_runtime_mode": self.runtime_mode,
            "runtime_state": (
                "RUNNING"
                if self.runtime_ready and self.runtime_mode != "OFF"
                else "STOPPED"
            ),
            "runtime_ready": self.runtime_ready,
            "runtime_blockers": list(self.runtime_blockers),
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
                body = {
                    **body,
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


class AstrBotControlPlaneRoutes:
    """Registers official AstrBot plugin routes without leaking its web framework."""

    PLUGIN_NAME = "astrbot_plugin_groupmate"
    ENDPOINTS = ControlPlaneWebAPI.QUERY_ENDPOINTS + ("avatar", "media", "commands", "events")

    def __init__(self, context: object, *, api_factory: Callable[[], object]) -> None:
        self.context = context
        self.api_factory = api_factory

    def register(self) -> None:
        registrar = getattr(self.context, "register_web_api", None)
        if not callable(registrar):
            raise RuntimeError("AstrBot Context does not support plugin Web APIs")
        for endpoint in self.ENDPOINTS:
            methods = ["POST"] if endpoint == "commands" else ["GET"]
            registrar(
                f"/{self.PLUGIN_NAME}/{endpoint}",
                self._handler(endpoint),
                methods,
                f"Groupmate control plane: {endpoint}",
            )

    def _handler(self, endpoint: str):
        async def handle():
            from astrbot.api.web import request, stream_response

            try:
                api = self.api_factory()
            except RuntimeError as exc:
                return {"error": "control_plane_unavailable", "detail": str(exc)}, 503
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
            return response.body, response.status

        return handle


__all__ = (
    "AstrBotControlPlaneRoutes",
    "ControlPlaneWebAPI",
    "WebRequest",
    "WebResponse",
)
