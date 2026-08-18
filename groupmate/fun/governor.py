"""Shared safety checks for optional fun feature plans."""

from __future__ import annotations

import re
from typing import Tuple

from .contracts import FunFeaturePlan


_URL = re.compile(r"https?://|www\.", re.I)


class FunGovernor:
    def __init__(self, *, max_public_chars: int = 32) -> None:
        self.max_public_chars = max(8, int(max_public_chars))

    def validate(self, plan: FunFeaturePlan) -> Tuple[bool, str]:
        if not isinstance(plan, FunFeaturePlan):
            return False, "invalid_plan"
        value = str(plan.public_value or "").strip()
        if not value:
            return False, "empty_public_value"
        if len(value) > self.max_public_chars:
            return False, "public_value_too_long"
        if "\n" in value or "\r" in value:
            return False, "public_value_multiline"
        if "@" in value:
            return False, "public_value_mentions"
        if _URL.search(value):
            return False, "public_value_url"
        if any(word in value for word in ("傻逼", "死妈", "滚", "废物")):
            return False, "public_value_abusive"
        return True, ""
