"""Validated, administrator-owned Groupmate persona profile."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


PERSONA_PROFILE_CONFIG_KEY = "persona_profile"


_SECTIONS = (
    "identity",
    "presence",
    "participation",
    "expression",
    "social",
    "media",
    "tools",
)

_FIELDS = {
    "identity": ("name", "role", "background"),
    "presence": ("default_mode", "rhythm"),
    "participation": ("initiative", "speak_when", "stay_silent_when"),
    "expression": ("tone", "reply_length", "language_habits", "emoji_style"),
    "social": ("stance", "relationship_style", "culture_adaptation"),
    "media": ("policy", "notes"),
    "tools": ("autonomy", "confirmation_policy"),
}

_CHOICES = {
    ("presence", "default_mode"): {"social", "quiet_observer"},
    ("participation", "initiative"): {"reserved", "balanced", "proactive"},
    ("expression", "reply_length"): {"short", "balanced", "detailed"},
    ("expression", "emoji_style"): {"none", "light", "natural"},
    ("media", "policy"): {"disabled", "contextual"},
    ("tools", "autonomy"): {"disabled", "read_only", "low_impact"},
}

_DEFAULT = {
    "identity": {
        "name": "Groupmate",
        "role": "群聊中的长期伙伴，先理解现场，再在有价值时自然参与。",
        "background": "熟悉群内关系和共同经历，但不冒充任何真实成员。",
    },
    "presence": {
        "default_mode": "social",
        "rhythm": "保持在场感，不追求每条消息都回应；忙碌或信息不足时安静观察。",
    },
    "participation": {
        "initiative": "balanced",
        "speak_when": "被直接询问、能够提供帮助、适合接住情绪或自然延续共同话题时参与。",
        "stay_silent_when": "外置插件正在处理、话题已自然结束、插话会打断他人或没有新增价值时沉默。",
    },
    "expression": {
        "tone": "自然、真诚、简洁，像熟悉群氛围的朋友而不是客服。",
        "reply_length": "short",
        "language_habits": "优先口语化短句；避免模板化总结、说教和重复复述。",
        "emoji_style": "light",
    },
    "social": {
        "stance": "友好但有边界，不虚构亲密关系。",
        "relationship_style": "关系随真实互动逐步形成，对不同成员保持连续但不过度迎合。",
        "culture_adaptation": "学习本群称呼、梗和节奏，但不复制敏感经历或跨群传播。",
    },
    "media": {
        "policy": "disabled",
        "notes": "只有媒体比文字更合适且来源与权限明确时才使用。",
    },
    "tools": {
        "autonomy": "read_only",
        "confirmation_policy": "只自主使用管理员允许的只读能力；外部副作用必须明确确认。",
    },
}


@dataclass(frozen=True)
class GroupmatePersonaProfile:
    sections: Mapping[str, Mapping[str, str]]

    @classmethod
    def default(cls) -> "GroupmatePersonaProfile":
        return cls.from_mapping(_DEFAULT)

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "GroupmatePersonaProfile":
        if not isinstance(value, Mapping):
            raise ValueError("persona profile must be an object")
        unknown_sections = set(value) - set(_SECTIONS)
        if unknown_sections:
            raise ValueError(
                f"unknown persona sections: {sorted(unknown_sections)}"
            )
        missing_sections = set(_SECTIONS) - set(value)
        if missing_sections:
            raise ValueError(
                f"missing persona sections: {sorted(missing_sections)}"
            )
        normalized: dict[str, Mapping[str, str]] = {}
        for section in _SECTIONS:
            raw_section = value[section]
            if not isinstance(raw_section, Mapping):
                raise ValueError(f"persona section must be an object: {section}")
            expected = set(_FIELDS[section])
            unknown = set(raw_section) - expected
            missing = expected - set(raw_section)
            if unknown:
                raise ValueError(
                    f"unknown persona fields in {section}: {sorted(unknown)}"
                )
            if missing:
                raise ValueError(
                    f"missing persona fields in {section}: {sorted(missing)}"
                )
            section_values: dict[str, str] = {}
            for field in _FIELDS[section]:
                text = str(raw_section[field] or "").strip()
                if not text:
                    raise ValueError(f"persona field must not be empty: {section}.{field}")
                if len(text) > 1200:
                    raise ValueError(f"persona field is too long: {section}.{field}")
                allowed = _CHOICES.get((section, field))
                if allowed is not None and text not in allowed:
                    raise ValueError(
                        f"unsupported {field}: {text}; expected one of {sorted(allowed)}"
                    )
                section_values[field] = text
            normalized[section] = MappingProxyType(section_values)
        return cls(MappingProxyType(normalized))

    @classmethod
    def from_behavior_config(
        cls, value: Mapping[str, object]
    ) -> "GroupmatePersonaProfile":
        raw = value.get(PERSONA_PROFILE_CONFIG_KEY)
        if raw is None:
            return cls.default()
        if not isinstance(raw, Mapping):
            raise ValueError("persona profile config must be an object")
        return cls.from_mapping(raw)

    def to_mapping(self) -> dict[str, dict[str, str]]:
        return copy.deepcopy(
            {
                section: dict(values)
                for section, values in self.sections.items()
            }
        )


__all__ = ("GroupmatePersonaProfile", "PERSONA_PROFILE_CONFIG_KEY")
