"""Strict JSONL contract for reproducible decision evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from ..models import ChatMessage, TriggerKind
from .models import EvaluationCase, EvaluationDataset, EvaluationLabel, ExpectedOutcome


class DatasetValidationError(ValueError):
    pass


def load_dataset(path: Path) -> EvaluationDataset:
    source = Path(path)
    cases: List[EvaluationCase] = []
    canonical: List[Dict[str, Any]] = []
    seen = set()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetValidationError("无法读取数据集：{}".format(exc))

    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise DatasetValidationError("第 {} 行不是合法 JSON：{}".format(line_number, exc))
        try:
            case = _parse_case(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise DatasetValidationError("第 {} 行校验失败：{}".format(line_number, exc))
        if case.case_id in seen:
            raise DatasetValidationError("case_id 重复：{}".format(case.case_id))
        seen.add(case.case_id)
        cases.append(case)
        canonical.append(data)

    if not cases:
        raise DatasetValidationError("数据集不能为空")
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return EvaluationDataset(tuple(cases), hashlib.sha256(encoded).hexdigest())


def _parse_case(data: Dict[str, Any]) -> EvaluationCase:
    if not isinstance(data, dict):
        raise ValueError("场景必须是对象")
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError("只支持 schema_version=1")
    case_id = str(data.get("case_id", "")).strip()
    if not case_id:
        raise ValueError("case_id 不能为空")
    raw_messages = data.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("messages 不能为空")
    messages = tuple(_parse_message(item) for item in raw_messages)
    group_ids = {message.group_id for message in messages}
    if len(group_ids) != 1:
        raise ValueError("场景消息必须属于同一个群")
    timestamps = [message.timestamp for message in messages]
    if timestamps != sorted(timestamps):
        raise ValueError("消息时间戳必须单调不降")

    expected_data = data.get("expected")
    if not isinstance(expected_data, dict):
        raise ValueError("expected 必须是对象")
    try:
        label = EvaluationLabel(str(expected_data.get("label", "")))
    except ValueError:
        raise ValueError("未知评测标签")
    try:
        triggers = tuple(
            TriggerKind(str(value)) for value in expected_data.get("allowed_triggers", [])
        )
    except ValueError:
        raise ValueError("allowed_triggers 包含未知触发类型")
    reasons = tuple(
        str(value).strip()
        for value in expected_data.get("allowed_reason_codes", [])
        if str(value).strip()
    )
    target = expected_data.get("target_message_id")
    return EvaluationCase(
        schema_version=1,
        case_id=case_id,
        description=str(data.get("description", "")).strip(),
        messages=messages,
        expected=ExpectedOutcome(
            label=label,
            allowed_triggers=triggers,
            allowed_reason_codes=reasons,
            target_message_id=str(target) if target is not None else None,
        ),
        tags=tuple(str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()),
        source=str(data.get("source", "handcrafted")).strip() or "handcrafted",
    )


def _parse_message(data: Dict[str, Any]) -> ChatMessage:
    if not isinstance(data, dict):
        raise ValueError("消息必须是对象")
    return ChatMessage(
        message_id=str(data["message_id"]),
        group_id=str(data["group_id"]),
        sender_id=str(data["sender_id"]),
        sender_name=str(data.get("sender_name", "")),
        text=str(data.get("text", "")),
        timestamp=int(data["timestamp"]),
        reply_to_message_id=(
            str(data["reply_to_message_id"])
            if data.get("reply_to_message_id") is not None
            else None
        ),
        reply_to_bot=bool(data.get("reply_to_bot", False)),
        mentions_bot=bool(data.get("mentions_bot", False)),
        is_bot=bool(data.get("is_bot", False)),
        is_command=bool(data.get("is_command", False)),
        image_urls=tuple(str(value) for value in data.get("image_urls", [])),
        segment_types=tuple(str(value) for value in data.get("segment_types", [])),
    )
