"""Framework-neutral control API plus the thin AstrBot Web API registrar."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from ..social_runtime.control.commands import (
    ApproveCalibration,
    CancelTask,
    CommandContext,
    CommandError,
    CommandService,
    CommandValidationError,
    CorrectSocialState,
    CreateConfigDraft,
    DryRunConfig,
    ExpectedVersionConflict,
    ForgetMemory,
    LinkIdentity,
    PauseRuntime,
    PublishConfig,
    ResetState,
    RestoreConfig,
    ReviewEvidence,
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
        "activity",
        "scenes",
        "people",
        "culture",
        "tasks",
        "persona",
        "governance",
        "evaluation",
        "health",
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
    ) -> None:
        self.queries = queries
        self.stream = stream
        self._command_service_for = command_service_for
        self._event_publisher = event_publisher
        self.persona_id = str(persona_id).strip()
        self.group_ids = frozenset(str(value).strip() for value in group_ids)
        if not self.persona_id or not self.group_ids:
            raise ValueError("control API requires persona and group scope")
        self._degraded: dict[str, str] = {}

    def mark_degraded(self, component: str, reason: str) -> None:
        name = str(component).strip()
        detail = str(reason).strip()
        if name and detail:
            self._degraded[name] = detail

    def clear_degraded(self, component: str) -> None:
        self._degraded.pop(str(component).strip(), None)

    async def handle(self, request: WebRequest) -> WebResponse:
        endpoint = str(request.path).strip().strip("/")
        if endpoint in self.QUERY_ENDPOINTS:
            if str(request.method).upper() != "GET":
                return self._error(405, "method_not_allowed")
            try:
                persona_id, group_id = self._scope(request)
                query = getattr(self.queries, endpoint)
                body = query(persona_id=persona_id, group_id=group_id)
            except LookupError:
                return self._error(404, "scope_not_found")
            except Exception as exc:
                self._degraded["query"] = str(exc)
                return self._error(503, "projection_query_unavailable", detail=str(exc))
            self._degraded.pop("query", None)
            if endpoint == "health":
                body = {
                    **body,
                    "degraded": bool(self._degraded),
                    "degraded_reasons": list(self._degraded.values()),
                    "fallback_poll_seconds": 15,
                }
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
            persona_id, group_id = self._scope(request)
            body = dict(request.json_body or {})
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

    def _scope(self, request: WebRequest) -> tuple[str, str]:
        persona_id = str(request.query.get("persona_id") or "").strip()
        group_id = str(request.query.get("group_id") or "").strip()
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
    ENDPOINTS = ControlPlaneWebAPI.QUERY_ENDPOINTS + ("commands", "events")

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
                    yield str(response.body)

                return stream_response(events())
            return response.body, response.status

        return handle


__all__ = (
    "AstrBotControlPlaneRoutes",
    "ControlPlaneWebAPI",
    "WebRequest",
    "WebResponse",
)
