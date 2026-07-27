"""Scope-aware memory retrieval。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from ..models import MemoryItem, MemoryScope, MemoryStatus


def search_memories(
    rows: Sequence[MemoryItem],
    *,
    query: str,
    now: int,
    limit: int,
    subject_id: Optional[str] = None,
    subject_ids: Optional[Sequence[str]] = None,
    include_user_in_group: bool = True,
) -> List[MemoryItem]:
    """只召回 accepted 且未过期；跨群由调用方保证 rows 已按 group_id 过滤。"""
    focus_subjects = {
        str(item) for item in (subject_ids or ()) if str(item).strip()
    }
    if subject_id:
        focus_subjects.add(str(subject_id))

    query_tokens = _tokens(query)
    query_grams = _char_ngrams(query)
    ranked: List[Tuple[float, MemoryItem]] = []
    for item in rows:
        if not _visible(item, now=now):
            continue
        if not _scope_allows(
            item,
            focus_subjects=focus_subjects,
            include_user_in_group=include_user_in_group,
        ):
            continue
        item_tokens = _tokens(item.text)
        item_grams = _char_ngrams(item.text)
        token_overlap = (
            len(query_tokens & item_tokens) / max(1, len(query_tokens))
            if query_tokens
            else 0.0
        )
        gram_overlap = (
            len(query_grams & item_grams) / max(1, len(query_grams))
            if query_grams
            else 0.0
        )
        overlap = max(token_overlap, gram_overlap * 0.9)
        age = max(0, int(now) - item.created_at)
        recency = max(0.0, 1.0 - age / (30 * 24 * 3600.0))
        authority = min(max(item.authority, 0), 10) / 10.0
        subject_boost = 0.06 if item.subject_id in focus_subjects else 0.0
        score = (
            overlap * 0.48
            + item.importance * 0.18
            + item.confidence * 0.12
            + authority * 0.08
            + recency * 0.08
            + subject_boost
        )
        if overlap > 0 or not query_tokens:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
    return [item for _, item in ranked[: max(0, int(limit))]]


def _visible(item: MemoryItem, *, now: int) -> bool:
    if item.status is not MemoryStatus.ACCEPTED:
        return False
    if item.expires_at is not None and int(item.expires_at) <= int(now):
        return False
    return True


def _scope_allows(
    item: MemoryItem,
    *,
    focus_subjects: set,
    include_user_in_group: bool,
) -> bool:
    scope = item.scope
    if not isinstance(scope, MemoryScope):
        try:
            scope = MemoryScope(str(scope))
        except ValueError:
            scope = MemoryScope.USER_IN_GROUP
    if scope is MemoryScope.GROUP:
        return True
    if scope is MemoryScope.SELF:
        return True
    if scope is MemoryScope.USER_IN_GROUP:
        if not include_user_in_group:
            return False
        if focus_subjects and item.subject_id not in focus_subjects:
            return False
        return True
    return False


def _tokens(text: str) -> set:
    lowered = (text or "").lower()
    latin = set(re.findall(r"[a-z0-9_]+", lowered))
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    latin.update(chinese)
    latin.update("".join(pair) for pair in zip(chinese, chinese[1:]))
    return latin


def _char_ngrams(text: str, size: int = 3) -> set:
    cleaned = re.sub(r"\s+", "", (text or "").lower())
    if len(cleaned) < size:
        return set(cleaned) if cleaned else set()
    return {
        cleaned[index : index + size] for index in range(len(cleaned) - size + 1)
    }
