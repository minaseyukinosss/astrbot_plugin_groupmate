"""Public, group-scoped affection leaderboard projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AffectionLeaderboardEntry:
    rank: int
    display_name: str
    platform_tail: str
    score: float
    stage: str
    is_requester: bool = False

    def public_mapping(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "display_name": self.display_name,
            "platform_tail": self.platform_tail,
            "score": self.score,
            "score_text": _score_text(self.score),
            "score_tone": (
                "positive"
                if self.score > 0
                else "negative"
                if self.score < 0
                else "neutral"
            ),
            "stage": self.stage,
            "is_requester": self.is_requester,
        }


@dataclass(frozen=True)
class AffectionLeaderboard:
    group_id: str
    group_name: str
    updated_at: int
    entries: tuple[AffectionLeaderboardEntry, ...]
    recent_active_count: int = 0
    roster_complete: bool = True

    @property
    def requester(self) -> AffectionLeaderboardEntry:
        return next(item for item in self.entries if item.is_requester)

    def public_context(
        self,
        entries: tuple[AffectionLeaderboardEntry, ...] | None = None,
    ) -> dict[str, object]:
        visible = self.entries if entries is None else tuple(entries)
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "updated_at": self.updated_at,
            "member_count": len(self.entries),
            "recent_active_count": self.recent_active_count,
            "active_count": self.recent_active_count,
            "roster_complete": self.roster_complete,
            "requester": self.requester.public_mapping(),
            "entries": [item.public_mapping() for item in visible],
        }

    def text_fallback(self) -> str:
        member = self.requester
        roster_note = (
            "\n成员名单暂未完全同步。" if not self.roster_complete else ""
        )
        return (
            f"{member.display_name}，你在本群好感度榜第 "
            f"{member.rank}/{len(self.entries)} 名：{_score_text(member.score)}"
            f"（{member.stage}）。{roster_note}\n"
            "榜单图片暂时生成失败，稍后再试也可以。"
        )


class AffectionLeaderboardService:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def build(
        self,
        *,
        persona_id: str,
        group_id: str,
        group_name: str,
        requester_id: str,
        members: tuple[Mapping[str, object], ...],
        updated_at: int,
        recent_active_count: int | None = None,
        roster_complete: bool = True,
    ) -> AffectionLeaderboard:
        persona = str(persona_id or "").strip()
        group = str(group_id or "").strip()
        requester = str(requester_id or "").strip()
        if not persona or not group or not requester:
            raise ValueError("leaderboard requires persona, group, and requester scope")
        deduplicated: dict[str, tuple[str, int]] = {}
        for member in members:
            actor_id = str(member.get("actor_id") or "").strip()
            if not actor_id:
                continue
            name = " ".join(
                str(member.get("display_name") or "群成员").split()
            )[:48]
            seen_at = max(0, int(member.get("updated_at") or 0))
            previous = deduplicated.get(actor_id)
            if previous is None or seen_at >= previous[1]:
                deduplicated[actor_id] = (name or "群成员", seen_at)
        deduplicated.setdefault(requester, ("你", int(updated_at)))

        ranked = []
        for actor_id, (display_name, seen_at) in deduplicated.items():
            _, affection = self._repository.relationship_snapshot(
                persona, group, actor_id
            )
            ranked.append(
                (
                    actor_id,
                    display_name,
                    seen_at,
                    float(affection.value),
                    affection.stage.value,
                )
            )
        ranked.sort(key=lambda item: (-item[3], -item[2], item[1], item[0]))
        entries = tuple(
            AffectionLeaderboardEntry(
                rank=index,
                display_name=item[1],
                platform_tail=item[0][-4:] or "—",
                score=item[3],
                stage=item[4],
                is_requester=item[0] == requester,
            )
            for index, item in enumerate(ranked, 1)
        )
        return AffectionLeaderboard(
            group_id=group,
            group_name=" ".join(str(group_name or "当前群聊").split())[:60]
            or "当前群聊",
            updated_at=max(0, int(updated_at)),
            entries=entries,
            recent_active_count=min(
                len(entries),
                max(
                    0,
                    len(entries)
                    if recent_active_count is None
                    else int(recent_active_count),
                ),
            ),
            roster_complete=bool(roster_complete),
        )


def _score_text(value: float) -> str:
    score = round(float(value), 1)
    return f"+{score:.1f}" if score > 0 else f"{score:.1f}"


__all__ = (
    "AffectionLeaderboard",
    "AffectionLeaderboardEntry",
    "AffectionLeaderboardService",
)
