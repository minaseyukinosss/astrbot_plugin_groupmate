from __future__ import annotations

import asyncio
import base64

from groupmate.adapters.web_api import ControlPlaneWebAPI, WebRequest
from groupmate.social_runtime.control.commands import CommandService
from groupmate.social_runtime.control.projections import ProjectionConsumer
from groupmate.social_runtime.control.queries import ProjectionQueries
from groupmate.social_runtime.control.stickers import StickerControlService
from groupmate.social_runtime.control.stream import ProjectionStream
from groupmate.social_runtime.persistence.schema import initialize_database
from tests.social_runtime.stickers.test_lexicon import png_bytes


def _api(tmp_path):
    path = tmp_path / "stickers-web.db"
    initialize_database(path)
    ProjectionConsumer(path, "runtime")
    return ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        sticker_queries=StickerControlService(
            path, tmp_path, persona_id="aemeath"
        ),
        stream=ProjectionStream(path),
        command_service_for=lambda username: CommandService(
            path,
            persona_id="aemeath",
            group_ids=("group-1",),
            admin_ids=(username,),
        ),
        event_publisher=lambda event: None,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )


def _request(path, *, method="GET", body=None, username="admin:root"):
    return WebRequest(
        method=method,
        path=path,
        query={"persona_id": "aemeath", "group_id": "group-1"},
        headers={"content-type": "application/json"} if method == "POST" else {},
        json_body=body,
        username=username,
    )


def test_sticker_upload_caption_confirm_and_preview_hide_paths(tmp_path):
    api = _api(tmp_path)
    content = png_bytes()
    uploaded = asyncio.run(
        api.handle(
            _request(
                "/stickers/actions",
                method="POST",
                body={
                    "type": "sticker_upload",
                    "filename": "shrug.png",
                    "mime_type": "image/png",
                    "license_status": "owned",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "meaning": "摊手无奈我也没办法",
                },
            )
        )
    )
    assert uploaded.status == 200
    asset_id = uploaded.body["item"]["asset_id"]
    assert uploaded.body["item"]["status"] == "candidate"
    assert "relative_path" not in uploaded.body["item"]
    confirmed = asyncio.run(
        api.handle(
            _request(
                "/stickers/actions",
                method="POST",
                body={"type": "sticker_confirm", "asset_id": asset_id},
            )
        )
    )
    assert confirmed.body["item"]["status"] == "ready"
    listing = asyncio.run(api.handle(_request("/stickers")))
    assert listing.status == 200
    assert listing.body["library"][0]["asset_id"] == asset_id
    assert listing.body["vision_enabled"] is False
    preview = asyncio.run(
        api.handle(
            WebRequest(
                method="GET",
                path="/stickers/preview",
                query={
                    "persona_id": "aemeath",
                    "group_id": "group-1",
                    "asset_id": asset_id,
                },
                headers={},
                json_body=None,
                username="admin:root",
            )
        )
    )
    assert preview.status == 200
    assert preview.body["data_uri"].startswith("data:image/png;base64,")
    assert "/persona_media/" not in str(preview.body)


def test_sticker_recaption_requires_vision_and_queues_when_enabled(tmp_path):
    queued = []
    path = tmp_path / "stickers-web.db"
    initialize_database(path)
    ProjectionConsumer(path, "runtime")
    api = ControlPlaneWebAPI(
        queries=ProjectionQueries(path),
        sticker_queries=StickerControlService(
            path,
            tmp_path,
            persona_id="aemeath",
            vision_enqueue=lambda asset_id, *, force=False: queued.append((asset_id, force)) or True,
            vision_enabled=lambda: True,
        ),
        stream=ProjectionStream(path),
        command_service_for=lambda username: CommandService(
            path,
            persona_id="aemeath",
            group_ids=("group-1",),
            admin_ids=(username,),
        ),
        event_publisher=lambda event: None,
        persona_id="aemeath",
        group_ids=("group-1",),
        admin_ids=("admin:root",),
    )
    content = png_bytes()
    uploaded = asyncio.run(
        api.handle(
            _request(
                "/stickers/actions",
                method="POST",
                body={
                    "type": "sticker_upload",
                    "filename": "shrug.png",
                    "mime_type": "image/png",
                    "license_status": "owned",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                },
            )
        )
    )
    asset_id = uploaded.body["item"]["asset_id"]
    recaption = asyncio.run(
        api.handle(
            _request(
                "/stickers/actions",
                method="POST",
                body={"type": "sticker_recaption", "asset_id": asset_id},
            )
        )
    )
    listing = asyncio.run(api.handle(_request("/stickers")))
    assert recaption.status == 200
    assert recaption.body["queued"] is True
    assert queued[-1] == (asset_id, True)
    assert listing.body["vision_enabled"] is True


def test_member_cannot_read_stickers(tmp_path):
    api = _api(tmp_path)
    denied = asyncio.run(
        api.handle(_request("/stickers", username="member:1"))
    )
    assert denied.status == 403
