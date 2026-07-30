"""Versioned, privacy-safe scenario and result contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from groupmate.models import ChatMessage, TopicSnapshot
from groupmate.policies import BehaviorPolicy, ReplyPolicy


SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
ASSEMBLY_VERSION = "companion-core-phase1"

_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")
_SYNTHETIC_ID_RE = re.compile(r"^(?:g|u|m|bot|task)[a-z0-9_-]{1,40}$")
_RAW_NUMERIC_ID_RE = re.compile(r"^\d{5,}$")
_CATEGORIES = frozenset(
    {"trigger", "guard", "single_turn", "multi_turn", "privacy"}
)
_ACTIONS = frozenset({"sent", "silent"})
_SCENARIO_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "category",
        "description",
        "messages",
        "expected",
        "scripted",
        "constraints",
        "policy",
        "tags",
        "model_enabled",
    }
)
_MESSAGE_KEYS = frozenset(
    {
        "message_id",
        "group_id",
        "sender_id",
        "sender_name",
        "text",
        "timestamp",
        "reply_to_message_id",
        "reply_to_bot",
        "mentions_bot",
        "is_bot",
        "is_command",
        "image_urls",
        "segment_types",
    }
)
_EXPECTED_KEYS = frozenset(
    {"trigger", "action", "outcome_reason", "guard_codes"}
)
_SCRIPTED_KEYS = frozenset({"output", "repair_output"})
_CONSTRAINT_KEYS = frozenset(
    {
        "min_chars",
        "max_chars",
        "required_patterns",
        "forbidden_patterns",
        "max_repeated_ratio",
    }
)
_POLICY_KEYS = frozenset()


class ScenarioValidationError(ValueError):
    """Raised when an evaluation fixture violates the public schema."""


def _unknown_keys(raw: Mapping[str, Any], allowed: Iterable[str], path: str) -> None:
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise ScenarioValidationError(
            "{} contains unknown fields: {}".format(path, ", ".join(unknown))
        )


def _require_string(raw: Mapping[str, Any], key: str, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError("{}.{} must be a non-empty string".format(path, key))
    return value.strip()


def _optional_string(raw: Mapping[str, Any], key: str, path: str) -> Optional[str]:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScenarioValidationError("{}.{} must be a string".format(path, key))
    return value.strip()


def _string_tuple(value: Any, path: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ScenarioValidationError("{} must be a string array".format(path))
    return tuple(item for item in (part.strip() for part in value) if item)


def _synthetic_id(value: str, path: str, *, allow_empty: bool = False) -> str:
    if not value and allow_empty:
        return ""
    if _RAW_NUMERIC_ID_RE.fullmatch(value):
        raise ScenarioValidationError("{} looks like a real numeric account ID".format(path))
    if not _SYNTHETIC_ID_RE.fullmatch(value):
        raise ScenarioValidationError(
            "{} must use a synthetic g/u/m/bot/task-prefixed ID".format(path)
        )
    return value


@dataclass(frozen=True)
class ScenarioMessage:
    message_id: str
    group_id: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: int
    reply_to_message_id: Optional[str] = None
    reply_to_bot: bool = False
    mentions_bot: bool = False
    is_bot: bool = False
    is_command: bool = False
    image_urls: Tuple[str, ...] = ()
    segment_types: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "ScenarioMessage":
        if not isinstance(raw, Mapping):
            raise ScenarioValidationError("{} must be an object".format(path))
        _unknown_keys(raw, _MESSAGE_KEYS, path)
        message_id = _synthetic_id(_require_string(raw, "message_id", path), path + ".message_id")
        group_id = _synthetic_id(_require_string(raw, "group_id", path), path + ".group_id")
        sender_id = _synthetic_id(
            _require_string(raw, "sender_id", path),
            path + ".sender_id",
        )
        sender_name = _require_string(raw, "sender_name", path)
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise ScenarioValidationError("{}.text must be a string".format(path))
        timestamp = raw.get("timestamp")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
            raise ScenarioValidationError(
                "{}.timestamp must be a non-negative integer".format(path)
            )
        reply_to = _optional_string(raw, "reply_to_message_id", path)
        if reply_to:
            reply_to = _synthetic_id(reply_to, path + ".reply_to_message_id")
        return cls(
            message_id=message_id,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text.strip(),
            timestamp=timestamp,
            reply_to_message_id=reply_to,
            reply_to_bot=bool(raw.get("reply_to_bot", False)),
            mentions_bot=bool(raw.get("mentions_bot", False)),
            is_bot=bool(raw.get("is_bot", False)),
            is_command=bool(raw.get("is_command", False)),
            image_urls=_string_tuple(raw.get("image_urls"), path + ".image_urls"),
            segment_types=_string_tuple(
                raw.get("segment_types"), path + ".segment_types"
            ),
        )

    def to_chat_message(self) -> ChatMessage:
        return ChatMessage(
            message_id=self.message_id,
            group_id=self.group_id,
            sender_id=self.sender_id,
            sender_name=self.sender_name,
            text=self.text,
            timestamp=self.timestamp,
            reply_to_message_id=self.reply_to_message_id,
            reply_to_bot=self.reply_to_bot,
            mentions_bot=self.mentions_bot,
            is_bot=self.is_bot,
            is_command=self.is_command,
            image_urls=self.image_urls,
            segment_types=self.segment_types,
        )


@dataclass(frozen=True)
class ScenarioExpected:
    trigger: Optional[str] = None
    action: Optional[str] = None
    outcome_reason: Optional[str] = None
    guard_codes: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "ScenarioExpected":
        if not isinstance(raw, Mapping):
            raise ScenarioValidationError("{} must be an object".format(path))
        _unknown_keys(raw, _EXPECTED_KEYS, path)
        trigger = _optional_string(raw, "trigger", path)
        action = _optional_string(raw, "action", path)
        if action is not None and action not in _ACTIONS:
            raise ScenarioValidationError(
                "{}.action must be sent or silent".format(path)
            )
        return cls(
            trigger=trigger,
            action=action,
            outcome_reason=_optional_string(raw, "outcome_reason", path),
            guard_codes=_string_tuple(raw.get("guard_codes"), path + ".guard_codes"),
        )


@dataclass(frozen=True)
class ScriptedResponse:
    output: str
    repair_output: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "ScriptedResponse":
        if not isinstance(raw, Mapping):
            raise ScenarioValidationError("{} must be an object".format(path))
        _unknown_keys(raw, _SCRIPTED_KEYS, path)
        output = raw.get("output", "")
        if not isinstance(output, str):
            raise ScenarioValidationError("{}.output must be a string".format(path))
        return cls(
            output=output,
            repair_output=_optional_string(raw, "repair_output", path),
        )


@dataclass(frozen=True)
class OutputConstraints:
    min_chars: int = 0
    max_chars: int = 60
    required_patterns: Tuple[str, ...] = ()
    forbidden_patterns: Tuple[str, ...] = ()
    max_repeated_ratio: float = 0.92

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str) -> "OutputConstraints":
        if not isinstance(raw, Mapping):
            raise ScenarioValidationError("{} must be an object".format(path))
        _unknown_keys(raw, _CONSTRAINT_KEYS, path)
        minimum = raw.get("min_chars", 0)
        maximum = raw.get("max_chars", 60)
        repeated = raw.get("max_repeated_ratio", 0.92)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            raise ScenarioValidationError("{}.min_chars is invalid".format(path))
        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum < max(1, minimum)
        ):
            raise ScenarioValidationError("{}.max_chars is invalid".format(path))
        if not isinstance(repeated, (int, float)) or not 0 <= float(repeated) <= 1:
            raise ScenarioValidationError(
                "{}.max_repeated_ratio is invalid".format(path)
            )
        return cls(
            min_chars=minimum,
            max_chars=maximum,
            required_patterns=_string_tuple(
                raw.get("required_patterns"), path + ".required_patterns"
            ),
            forbidden_patterns=_string_tuple(
                raw.get("forbidden_patterns"), path + ".forbidden_patterns"
            ),
            max_repeated_ratio=float(repeated),
        )


@dataclass(frozen=True)
class Scenario:
    schema_version: int
    scenario_id: str
    category: str
    description: str
    messages: Tuple[ScenarioMessage, ...]
    expected: ScenarioExpected
    scripted: ScriptedResponse
    constraints: OutputConstraints
    policy: Mapping[str, Any] = field(default_factory=dict)
    tags: Tuple[str, ...] = ()
    model_enabled: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], path: str = "scenario") -> "Scenario":
        if not isinstance(raw, Mapping):
            raise ScenarioValidationError("{} must be an object".format(path))
        _unknown_keys(raw, _SCENARIO_KEYS, path)
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ScenarioValidationError(
                "{}.schema_version must be {}".format(path, SCHEMA_VERSION)
            )
        scenario_id = _require_string(raw, "id", path)
        if not _SCENARIO_ID_RE.fullmatch(scenario_id):
            raise ScenarioValidationError("{}.id has an invalid format".format(path))
        category = _require_string(raw, "category", path)
        if category not in _CATEGORIES:
            raise ScenarioValidationError(
                "{}.category is not supported".format(path)
            )
        description = _require_string(raw, "description", path)
        raw_messages = raw.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ScenarioValidationError("{}.messages must be non-empty".format(path))
        messages = tuple(
            ScenarioMessage.from_dict(item, "{}.messages[{}]".format(path, index))
            for index, item in enumerate(raw_messages)
        )
        group_ids = {message.group_id for message in messages}
        if len(group_ids) != 1:
            raise ScenarioValidationError(
                "{}.messages must belong to one synthetic group".format(path)
            )
        raw_policy = raw.get("policy", {})
        if not isinstance(raw_policy, Mapping):
            raise ScenarioValidationError("{}.policy must be an object".format(path))
        _unknown_keys(raw_policy, _POLICY_KEYS, path + ".policy")
        model_enabled = raw.get("model_enabled", True)
        if not isinstance(model_enabled, bool):
            raise ScenarioValidationError(
                "{}.model_enabled must be boolean".format(path)
            )
        return cls(
            schema_version=version,
            scenario_id=scenario_id,
            category=category,
            description=description,
            messages=messages,
            expected=ScenarioExpected.from_dict(
                raw.get("expected", {}), path + ".expected"
            ),
            scripted=ScriptedResponse.from_dict(
                raw.get("scripted", {"output": ""}), path + ".scripted"
            ),
            constraints=OutputConstraints.from_dict(
                raw.get("constraints", {}), path + ".constraints"
            ),
            policy=dict(raw_policy),
            tags=_string_tuple(raw.get("tags"), path + ".tags"),
            model_enabled=model_enabled,
        )

    def topic_snapshot(self) -> TopicSnapshot:
        messages = tuple(message.to_chat_message() for message in self.messages)
        return TopicSnapshot(
            topic_id="topic-" + self.scenario_id,
            group_id=messages[-1].group_id,
            messages=messages,
            created_at=messages[0].timestamp,
            updated_at=messages[-1].timestamp,
        )

    def behavior_policy(self) -> BehaviorPolicy:
        """Return deterministic internal policy for this isolated scenario."""

        if self.policy:
            raise ScenarioValidationError(
                "scenario.policy must be empty; use constraints for assertions"
            )
        return BehaviorPolicy(
            reply=ReplyPolicy(humanize_delay_enabled=False),
        )


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    expected: Any
    actual: Any


@dataclass(frozen=True)
class EvaluationResult:
    schema_version: int
    scenario_id: str
    category: str
    repetition: int
    mode: str
    prompt_version: str
    trigger: str
    sent: bool
    outcome_reason: str
    output_text: str
    guard_codes: Tuple[str, ...]
    checks: Tuple[CheckResult, ...]
    latency_ms: float
    llm_judge: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def load_scenarios(path: Path) -> Tuple[Scenario, ...]:
    scenarios: List[Scenario] = []
    seen = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ScenarioValidationError(
                    "{}:{} contains invalid JSON: {}".format(path, line_number, exc)
                )
            scenario = Scenario.from_dict(raw, "{}:{}".format(path, line_number))
            if scenario.scenario_id in seen:
                raise ScenarioValidationError(
                    "{}:{} duplicates scenario id {}".format(
                        path, line_number, scenario.scenario_id
                    )
                )
            seen.add(scenario.scenario_id)
            scenarios.append(scenario)
    if not scenarios:
        raise ScenarioValidationError("{} contains no scenarios".format(path))
    return tuple(scenarios)


def compute_prompt_version(
    persona_paths: Sequence[Path],
    model_config: Optional[Mapping[str, Any]] = None,
    assembly_version: str = ASSEMBLY_VERSION,
) -> str:
    digest = hashlib.sha256()
    digest.update(assembly_version.encode("utf-8"))
    for path in sorted((Path(item) for item in persona_paths), key=lambda item: str(item)):
        digest.update(str(path.name).encode("utf-8"))
        digest.update(path.read_bytes())
    safe_config = {
        key: value
        for key, value in dict(model_config or {}).items()
        if "key" not in key.lower() and "secret" not in key.lower()
    }
    digest.update(
        json.dumps(
            safe_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return digest.hexdigest()
