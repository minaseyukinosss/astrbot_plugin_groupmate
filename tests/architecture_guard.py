"""Executable clean-slate dependency guard used by release gates."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REMOVED_LEGACY_PACKAGES = (
    "engine",
    "core",
    "social",
    "memory",
    "host",
    "tools",
    "capabilities",
    "persona",
)


def main() -> None:
    remaining = [
        name for name in REMOVED_LEGACY_PACKAGES if (ROOT / "groupmate" / name).exists()
    ]
    if remaining:
        raise SystemExit(f"legacy packages reintroduced: {', '.join(remaining)}")

    forbidden = []
    for path in (ROOT / "groupmate" / "social_runtime").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            if any(name.startswith("astrbot") for name in names):
                forbidden.append(str(path.relative_to(ROOT)))
    if forbidden:
        raise SystemExit(
            "domain runtime imports AstrBot directly: " + ", ".join(sorted(set(forbidden)))
        )


if __name__ == "__main__":
    main()
