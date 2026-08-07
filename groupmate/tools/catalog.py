"""Runtime discovery and retrieval for AstrBot and Groupmate tools."""

from __future__ import annotations

import inspect
import re
import types
import typing
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .contracts import ToolDescriptor, ToolRisk, ToolSource


_DANGEROUS_WORDS = (
    "禁言",
    "踢",
    "拉黑",
    "封禁",
    "删除",
    "撤回",
    "管理员",
    "上管",
    "下管",
    "改群",
    "群名",
    "头衔",
    "批准",
    "拒绝",
    "上传",
    "写入",
    "转账",
    "支付",
    "发送",
    "转发",
    "私聊",
    "跨群",
    "execute",
    "delete",
    "ban",
    "kick",
    "admin",
    "send",
    "forward",
)
_READ_ONLY_WORDS = (
    "分析",
    "总结",
    "查看",
    "查询",
    "搜索",
    "状态",
    "信息",
    "天气",
    "歌词",
    "列表",
    "读取",
    "analysis",
    "summary",
    "search",
    "query",
    "list",
    "get_",
)
_NORMAL_WORDS = (
    "点歌",
    "播放",
    "音乐",
    "提醒",
    "抽签",
    "邮件",
    "邮箱",
    "祝福",
    "music",
    "play",
    "remind",
    "mail",
    "email",
)
_PASSTHROUGH_SEND_WORDS = (
    "点歌",
    "播放",
    "音乐",
    "歌曲",
    "图片",
    "文件",
    "语音",
    "报告",
    "分析",
    "邮件",
    "邮箱",
    "music",
    "play_song",
    "image",
    "file",
    "report",
    "analysis",
    "mail",
    "email",
)


class UniversalToolCatalog:
    """Build a version-tolerant snapshot of currently active host tools."""

    def __init__(
        self,
        context: Any,
        *,
        command_bridge_enabled: bool = True,
        exclude_module_prefixes: Sequence[str] = (
            "data.plugins.astrbot_plugin_groupmate",
        ),
        builtin_tools: Sequence[ToolDescriptor] = (),
    ) -> None:
        self.context = context
        self.command_bridge_enabled = bool(command_bridge_enabled)
        self.exclude_module_prefixes = tuple(exclude_module_prefixes)
        self._builtin_tools: Tuple[ToolDescriptor, ...] = tuple(builtin_tools or ())
        self._items: Dict[str, ToolDescriptor] = {}

    def set_builtin_tools(self, tools: Sequence[ToolDescriptor]) -> None:
        self._builtin_tools = tuple(tools or ())

    def refresh(self) -> Tuple[ToolDescriptor, ...]:
        items: Dict[str, ToolDescriptor] = {}
        for descriptor in self._builtin_tools:
            items[descriptor.tool_id] = descriptor
        for descriptor in self._discover_llm_tools():
            items[descriptor.tool_id] = descriptor
        if self.command_bridge_enabled:
            for descriptor in self._discover_commands():
                items[descriptor.tool_id] = descriptor
        self._items = items
        return self.all()

    def all(self) -> Tuple[ToolDescriptor, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (item.source.value, item.plugin_name, item.name),
            )
        )

    def get(self, tool_id: str) -> ToolDescriptor | None:
        return self._items.get(str(tool_id or "").strip())

    def retrieve(
        self,
        query: str,
        limit: int = 8,
        *,
        min_score: int = 3,
    ) -> Tuple[ToolDescriptor, ...]:
        text = _normalize(query)
        if not text:
            return ()
        query_terms = _terms(text)
        ranked = []
        for item in self._items.values():
            if not item.compatible:
                continue
            haystack = _normalize(
                " ".join(
                    (
                        item.name,
                        *item.aliases,
                        item.description,
                        item.plugin_name,
                    )
                )
            )
            score = 0
            if item.name and _normalize(item.name) in text:
                score += 20
            for alias in item.aliases:
                if _normalize(alias) in text:
                    score += 16
            haystack_terms = _terms(haystack)
            score += len(query_terms.intersection(haystack_terms)) * 3
            score += sum(1 for char in set(text) if char in haystack)
            score += _semantic_score(text, haystack)
            if score >= max(1, int(min_score)):
                ranked.append((score, item))
        ranked.sort(
            key=lambda pair: (
                -pair[0],
                0 if pair[1].source is ToolSource.LLM_TOOL else 1,
                pair[1].tool_id,
            )
        )
        return tuple(item for _, item in ranked[: max(1, int(limit))])

    def _discover_llm_tools(self) -> Iterable[ToolDescriptor]:
        getter = getattr(self.context, "get_llm_tool_manager", None)
        if not callable(getter):
            return ()
        try:
            manager = getter()
            tool_set = manager.get_full_tool_set()
        except Exception:
            return ()
        discovered: List[ToolDescriptor] = []
        for tool in tuple(getattr(tool_set, "tools", ()) or ()):
            if not bool(getattr(tool, "active", True)):
                continue
            module_path = str(getattr(tool, "handler_module_path", "") or "")
            if self._excluded(module_path):
                continue
            name = str(getattr(tool, "name", "") or "").strip()
            if not name:
                continue
            permission = "member"
            default_permission = getattr(manager, "_default_permission", None)
            if callable(default_permission):
                try:
                    permission = str(default_permission(name) or "member")
                except Exception:
                    permission = "member"
            description = str(getattr(tool, "description", "") or "").strip()
            discovered.append(
                ToolDescriptor(
                    tool_id="llm:" + name,
                    name=name,
                    description=description,
                    source=ToolSource.LLM_TOOL,
                    parameters=dict(getattr(tool, "parameters", {}) or {}),
                    plugin_name=_plugin_name(module_path),
                    handler_module_path=module_path,
                    permission=permission,
                    risk=_classify_risk(name, description),
                    timeout_seconds=_timeout_for_tool(
                        name,
                        description,
                        background=bool(getattr(tool, "is_background_task", False)),
                    ),
                    compatible=bool(module_path or callable(getattr(tool, "handler", None))),
                    compatibility_reason=(
                        ""
                        if module_path or callable(getattr(tool, "handler", None))
                        else "tool_context_not_supported"
                    ),
                    passthrough_send=_uses_passthrough_send(name, description),
                    native=tool,
                )
            )
        return tuple(discovered)

    def _discover_commands(self) -> Iterable[ToolDescriptor]:
        try:
            from astrbot.core.star.star_handler import EventType, star_handlers_registry
        except Exception:
            return ()
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent
            )
        except Exception:
            return ()
        discovered: List[ToolDescriptor] = []
        for metadata in handlers:
            module_path = str(
                getattr(metadata, "handler_module_path", "") or ""
            )
            if self._excluded(module_path):
                continue
            command_filter = next(
                (
                    item
                    for item in (getattr(metadata, "event_filters", ()) or ())
                    if item.__class__.__name__ == "CommandFilter"
                ),
                None,
            )
            if command_filter is None:
                continue
            names = tuple(
                str(item).strip()
                for item in (
                    getattr(command_filter, "get_complete_command_names", lambda: ())()
                    or ()
                )
                if str(item).strip()
            )
            if not names:
                continue
            name = str(getattr(command_filter, "command_name", "") or names[0]).strip()
            description = str(getattr(metadata, "desc", "") or "").strip()
            if not description:
                description = inspect.getdoc(getattr(metadata, "handler", None)) or ""
            permission = _command_permission(metadata)
            parameters = _command_parameters(
                command_filter,
                getattr(metadata, "handler", None),
            )
            incompatible = _command_incompatibility(metadata)
            discovered.append(
                ToolDescriptor(
                    tool_id="command:" + str(getattr(metadata, "handler_full_name", name)),
                    name=name,
                    aliases=tuple(item for item in names if item != name),
                    description=description,
                    source=ToolSource.COMMAND,
                    parameters=parameters,
                    plugin_name=_plugin_name(module_path),
                    handler_module_path=module_path,
                    permission=permission,
                    risk=_classify_risk(name, description),
                    timeout_seconds=_timeout_for_tool(name, description),
                    compatible=not incompatible,
                    compatibility_reason=incompatible,
                    passthrough_send=_uses_passthrough_send(name, description),
                    native=(metadata, command_filter),
                )
            )
        return tuple(discovered)

    def _excluded(self, module_path: str) -> bool:
        parts = tuple(part for part in str(module_path or "").split(".") if part)
        return "astrbot_plugin_groupmate" in parts or any(
            str(module_path or "").startswith(prefix)
            for prefix in self.exclude_module_prefixes
            if prefix
        )


def _command_permission(metadata: Any) -> str:
    for item in getattr(metadata, "event_filters", ()) or ():
        if item.__class__.__name__ != "PermissionTypeFilter":
            continue
        value = getattr(item, "permission_type", "")
        normalized = str(getattr(value, "value", value) or "").lower()
        if "admin" in normalized:
            return "admin"
    return "member"


def _command_parameters(
    command_filter: Any,
    handler: Any = None,
) -> Mapping[str, Any]:
    properties: Dict[str, Any] = {}
    required = []
    signature_params = _handler_signature_params(handler)
    declared = dict(getattr(command_filter, "handler_params", {}) or {})
    names = list(declared)
    if not names:
        names = [
            name
            for name in signature_params
            if name not in ("self", "cls", "event", "context")
        ]
    for name in names:
        param = signature_params.get(str(name))
        raw = declared.get(name)
        annotation = (
            param.annotation
            if param is not None and param.annotation is not inspect.Parameter.empty
            else raw
        )
        default = (
            param.default
            if param is not None
            else (
                raw
                if raw is not None
                and not _is_annotation_value(raw)
                and raw is not inspect.Parameter.empty
                else inspect.Parameter.empty
            )
        )
        if annotation is None or annotation is inspect.Parameter.empty:
            annotation = raw if _is_annotation_value(raw) else str
        prop: Dict[str, Any] = {"type": _json_type(annotation)}
        has_default = default is not inspect.Parameter.empty
        if has_default:
            if default is not None and not callable(default):
                prop["default"] = default
            elif default is None:
                prop["default"] = None
        properties[str(name)] = prop
        if not has_default:
            required.append(str(name))
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _handler_signature_params(handler: Any) -> Dict[str, inspect.Parameter]:
    if not callable(handler):
        return {}
    try:
        return dict(inspect.signature(handler).parameters)
    except (TypeError, ValueError):
        return {}


def _is_annotation_value(value: Any) -> bool:
    return (
        value is inspect.Parameter.empty
        or isinstance(value, type)
        or isinstance(value, types.UnionType)
        or typing.get_origin(value) is not None
    )


def _json_type(value: Any) -> str:
    target = value
    origin = typing.get_origin(target)
    if origin in (typing.Union, types.UnionType):
        args = [item for item in typing.get_args(target) if item is not type(None)]
        target = args[0] if len(args) == 1 else str
    if not isinstance(target, type):
        target = type(target) if target is not None else str
    if target is bool:
        return "boolean"
    if target is int:
        return "integer"
    if target is float:
        return "number"
    if target in (list, tuple, set):
        return "array"
    if target is dict:
        return "object"
    return "string"


def _command_incompatibility(metadata: Any) -> str:
    handler = getattr(metadata, "handler", None)
    if not callable(handler):
        return "handler_not_callable"
    description = " ".join(
        (
            str(getattr(metadata, "desc", "") or ""),
            inspect.getdoc(handler) or "",
        )
    ).lower()
    if any(word in description for word in ("session_waiter", "等待下一条", "交互式")):
        return "interactive_command"
    return ""


def _classify_risk(name: str, description: str) -> ToolRisk:
    text = (str(name) + " " + str(description)).lower()
    if any(word.lower() in text for word in _DANGEROUS_WORDS):
        return ToolRisk.DANGEROUS
    if any(word.lower() in text for word in _READ_ONLY_WORDS):
        return ToolRisk.READ_ONLY
    if any(word.lower() in text for word in _NORMAL_WORDS):
        return ToolRisk.NORMAL
    return ToolRisk.UNKNOWN


def _uses_passthrough_send(name: str, description: str) -> bool:
    text = (str(name) + " " + str(description)).lower()
    return any(word.lower() in text for word in _PASSTHROUGH_SEND_WORDS)


_LONG_RUNNING_WORDS = (
    "分析",
    "总结",
    "报告",
    "复盘",
    "analysis",
    "summary",
    "report",
)


def _timeout_for_tool(
    name: str,
    description: str,
    *,
    background: bool = False,
) -> float:
    text = (str(name) + " " + str(description)).lower()
    if any(word.lower() in text for word in _LONG_RUNNING_WORDS):
        return 300.0
    if background:
        return 120.0
    return 90.0


def _plugin_name(module_path: str) -> str:
    parts = [part for part in str(module_path or "").split(".") if part]
    return next(
        (part for part in reversed(parts) if part.startswith("astrbot_plugin_")),
        parts[-1] if parts else "",
    )


def _normalize(value: str) -> str:
    return re.sub(r"[\s/_\-:：，,。.!！?？]+", "", str(value or "").lower())


def _terms(value: str) -> set[str]:
    normalized = _normalize(value)
    terms = set(re.findall(r"[a-z0-9]{2,}", normalized))
    terms.update(
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
    )
    return {item for item in terms if item}


def _semantic_score(query: str, haystack: str) -> int:
    synonym_groups = (
        ("总结", "分析", "归纳", "复盘", "概括"),
        (
            "点歌",
            "音乐",
            "歌曲",
            "播放",
            "唱歌",
            "放一首",
            "放首",
            "来一首",
            "听一首",
            "播一首",
            "听歌",
            "放歌",
        ),
        ("取名", "取个名", "起名", "起个名", "改名", "改昵称", "群名片", "改名片"),
        ("禁言", "闭嘴", "安静", "mute", "ban"),
        ("踢人", "踢出", "移出", "kick"),
        ("天气", "气温", "下雨", "晴天"),
        ("搜索", "查询", "查找", "搜一下", "查一下"),
        ("转发", "发送", "发给", "传话"),
        ("邮件", "邮箱", "QQ邮箱", "发邮件", "寄邮件", "祝福邮件", "冒充", "整蛊", "客服"),
    )
    score = 0
    for group in synonym_groups:
        if any(word in query for word in group) and any(
            word in haystack for word in group
        ):
            score += 12
    if any(word in query for word in ("群消息", "群聊", "聊天记录")) and any(
        word in haystack for word in ("群消息", "群聊", "群分析")
    ):
        score += 8
    return score
