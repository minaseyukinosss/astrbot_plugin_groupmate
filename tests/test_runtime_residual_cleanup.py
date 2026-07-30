from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PATHS = (
    ROOT / "groupmate" / "engine" / "workflow.py",
    ROOT / "groupmate" / "engine" / "delivery.py",
    ROOT / "groupmate" / "engine" / "runtime.py",
    ROOT / "groupmate" / "core" / "relationships.py",
    ROOT / "groupmate" / "core" / "__init__.py",
    ROOT / "groupmate" / "core" / "projections.py",
    ROOT / "groupmate" / "host" / "bridge.py",
    ROOT / "groupmate" / "host" / "llm.py",
    ROOT / "groupmate" / "memory" / "memory_writer.py",
    ROOT / "groupmate" / "memory" / "store.py",
    ROOT / "groupmate" / "ports.py",
    ROOT / "eval" / "adapters.py",
    ROOT / "eval" / "runner.py",
    ROOT / "eval" / "shadow_export.py",
    ROOT / "eval" / "shadow_projector.py",
)

FORBIDDEN_BY_PATH = {
    ROOT / "groupmate" / "engine" / "workflow.py": (
        "getattr(self.memory,",
        "getattr(self.persona,",
    ),
    ROOT / "groupmate" / "engine" / "runtime.py": (
        "getattr(self.workflow,",
        "getattr(memory,",
    ),
    ROOT / "groupmate" / "core" / "projections.py": (
        "getattr(self.store,",
        "getattr(workflow,",
    ),
    ROOT / "groupmate" / "host" / "bridge.py": ("getattr(self.memory,",),
    ROOT / "groupmate" / "host" / "llm.py": ("getattr(self.persona,",),
    ROOT / "groupmate" / "memory" / "memory_writer.py": ("getattr(self.store,",),
}


def test_runtime_no_longer_contains_legacy_adapter_paths():
    forbidden = (
        "_is_legacy_task_resolution",
        "send_segments",
        "send_text",
        "Temporary adapter",
        "N-1 PlatformPort",
        "except TypeError:",
        "\"aemeath\",",
        "mark_outbox_sent",
        "parse_relationships",
    )

    offenders = []
    for path in PRODUCTION_PATHS:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {token!r}")
        for token in FORBIDDEN_BY_PATH.get(path, ()):
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {token!r}")

    assert offenders == []
