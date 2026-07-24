"""口吻锚点：从 Persona Pack 读取每轮紧凑角色快照。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

VOICE_BEHAVIOR_NOTE = (
    "口吻只决定怎么说，不决定做不做："
    "该回应的回应；不拿性格当乱沉默或乱怼的借口。"
)


def load_voice_anchor(pack_dir: Path, cache: Optional[dict] = None) -> str:
    """Load voice_anchor.md from pack; empty if missing."""
    path = Path(pack_dir) / "voice_anchor.md"
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
    if cache is not None:
        cache[key] = text
    return text


def format_voice_anchor_block(anchor: str, character_name: str) -> str:
    cleaned = (anchor or "").strip()
    if not cleaned:
        cleaned = f"{character_name}：短、自然、有态度；默认不反问"
    return (
        f"<voice_anchor>（角色快照：{cleaned}。{VOICE_BEHAVIOR_NOTE}）</voice_anchor>"
    )
