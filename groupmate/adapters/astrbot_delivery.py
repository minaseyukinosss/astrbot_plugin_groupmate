"""AstrBot platform lookup boundary for OneBot group delivery."""

from __future__ import annotations

from typing import Mapping

from .onebot_delivery import PermanentOneBotError


class AstrBotOneBotSender:
    def __init__(self, context: object) -> None:
        self.context = context

    async def __call__(
        self,
        *,
        group_id: str,
        segments: list[dict[str, object]],
        idempotency_key: str,
        platform_id: str,
        self_id: str | None = None,
    ) -> Mapping[str, object]:
        del idempotency_key
        get_platform = getattr(self.context, "get_platform_inst", None)
        platform = get_platform(platform_id) if callable(get_platform) else None
        get_client = getattr(platform, "get_client", None)
        client = get_client() if callable(get_client) else None
        send_group_msg = getattr(client, "send_group_msg", None)
        if not callable(send_group_msg):
            raise PermanentOneBotError("onebot_platform_unavailable")
        normalized_group: int | str = (
            int(group_id) if str(group_id).isdigit() else str(group_id)
        )
        routing = {"self_id": self_id} if self_id else {}
        return await send_group_msg(
            group_id=normalized_group,
            message=segments,
            **routing,
        )


__all__ = ("AstrBotOneBotSender",)
