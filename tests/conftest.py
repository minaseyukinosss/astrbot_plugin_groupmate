from __future__ import annotations

import pytest


_PATH_MARKERS = {
    "shared": "shared",
    "social_runtime": "social_runtime",
    "recovery": "recovery",
    "contracts": "contracts",
    "scenarios": "scenarios",
    "evaluation": "evaluation",
    "page": "page",
}


def pytest_collection_modifyitems(items):
    """Keep directory taxonomy and release-gate markers in sync."""

    for item in items:
        parts = item.path.parts
        for directory, marker_name in _PATH_MARKERS.items():
            if directory in parts:
                item.add_marker(getattr(pytest.mark, marker_name))
                break
