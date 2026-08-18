"""Composition-root boundaries for the clean Social Runtime implementation."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_INTERNAL_IMPORTS = (
    "groupmate.adapters",
    "groupmate.settings",
    "groupmate.social_runtime",
)


def _internal_imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module.startswith("groupmate"):
                imports.append(module)
        elif isinstance(node, ast.Import):
            imports.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("groupmate")
            )
    return tuple(imports)


def test_composition_root_only_depends_on_v2_boundaries():
    imports = _internal_imports(ROOT / "main.py")

    assert imports
    assert all(
        module == "groupmate.settings"
        or module.startswith(ALLOWED_INTERNAL_IMPORTS)
        for module in imports
    ), imports
