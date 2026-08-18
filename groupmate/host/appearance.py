"""Platform appearance actions used by optional fun features."""

from __future__ import annotations

from typing import Any, Callable, List


class HostAppearancePort:
    def __init__(
        self,
        context: Any,
        bot_id_getter: Callable[[str], str],
    ) -> None:
        self.context = context
        self.bot_id_getter = bot_id_getter

    async def set_own_group_card(self, group_id: str, card: str) -> str:
        gid = str(group_id or "").strip()
        card_text = str(card or "").strip()
        bot_id = str(self.bot_id_getter(gid) or "").strip()
        if not gid:
            return "group_id_missing"
        if not bot_id:
            return "bot_id_missing"
        if not card_text:
            return "card_empty"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return "aiocqhttp_client_unavailable"
        call = getattr(client, "call_action", None)
        if not callable(call):
            return "aiocqhttp_call_unavailable"
        group_value = int(gid) if gid.isdigit() else gid
        user_value = int(bot_id) if bot_id.isdigit() else bot_id
        try:
            await call(
                "set_group_card",
                group_id=group_value,
                user_id=user_value,
                card=card_text,
            )
        except Exception:
            return "set_group_card_failed"
        return ""

    def _resolve_aiocqhttp_client(self) -> Any:
        context = self.context
        for attr in ("get_platform", "get_platform_inst"):
            getter = getattr(context, attr, None)
            if not callable(getter):
                continue
            try:
                platform = getter("aiocqhttp")
            except Exception:
                platform = None
            client = getattr(platform, "get_client", None)
            if callable(client):
                try:
                    return client()
                except Exception:
                    pass
            bot = getattr(platform, "bot", None)
            if bot is not None:
                return bot
        platforms = getattr(context, "platforms", None) or getattr(
            context, "platform_manager", None
        )
        values: List[Any] = []
        if isinstance(platforms, dict):
            values = list(platforms.values())
        elif platforms is not None:
            get_insts = getattr(platforms, "get_insts", None)
            if callable(get_insts):
                try:
                    values = list(get_insts() or ())
                except Exception:
                    values = []
        for platform in values:
            name = str(getattr(getattr(platform, "meta", lambda: None)(), "name", "") or "")
            if not name:
                meta = getattr(platform, "metadata", None) or getattr(
                    platform, "meta_data", None
                )
                name = str(getattr(meta, "name", "") or "")
            if name and name.casefold() != "aiocqhttp":
                continue
            client = getattr(platform, "get_client", None)
            if callable(client):
                try:
                    return client()
                except Exception:
                    pass
            bot = getattr(platform, "bot", None)
            if bot is not None:
                return bot
        return None
