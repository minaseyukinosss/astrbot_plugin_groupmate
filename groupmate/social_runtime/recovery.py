"""Offline disaster-recovery primitives for Social Runtime v2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .persistence.schema import connect_database, verify_schema


def backup_v2_database(source_path: Path, destination_path: Path) -> Path:
    """Create a consistent SQLite backup after the operator has paused writes."""

    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == destination.resolve():
        raise ValueError("backup destination must differ from the source database")
    if destination.exists():
        raise FileExistsError(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(source) as source_db:
        verify_schema(source_db)
        target_db = sqlite3.connect(str(destination))
        try:
            source_db.backup(target_db)
            target_db.commit()
        finally:
            target_db.close()

    with connect_database(destination) as restored_db:
        verify_schema(restored_db)
    return destination


__all__ = ("backup_v2_database",)
