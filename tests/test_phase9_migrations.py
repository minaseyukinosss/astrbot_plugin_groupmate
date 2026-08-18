import sqlite3

from groupmate.memory.migrations import (
    SCHEMA_VERSION,
    _bootstrap_v11,
    _v11_to_v12,
    _v12_to_v13,
    _v13_to_v14,
    _v14_to_v15,
    _v15_to_v16,
    _v16_to_v17,
)
from groupmate.memory.store import SQLiteMemoryStore


def test_v17_database_migrates_through_commitment_scheduler_fields(tmp_path):
    path = tmp_path / "legacy-v17.db"
    db = sqlite3.connect(str(path))
    with db:
        _bootstrap_v11(db)
        _v11_to_v12(db)
        _v12_to_v13(db)
        _v13_to_v14(db)
        _v14_to_v15(db)
        _v15_to_v16(db)
        _v16_to_v17(db)
        db.execute(
            "INSERT INTO self_commitments("
            "commitment_id, persona_id, group_id, beneficiary_subject_id, summary, "
            "source_decision_id, source_message_id, source_quote, created_at, updated_at, "
            "status, required_capability, due_at, confidence, extractor_version"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-capability",
                "aemeath",
                "g1",
                "u1",
                "识别图片后告诉小明",
                "d1",
                "m1",
                "我看完告诉你",
                100,
                100,
                "pending",
                "vision.analyze",
                200,
                0.9,
                "legacy",
            ),
        )
        db.execute("UPDATE schema_meta SET value='17' WHERE key='version'")
    db.close()

    store = SQLiteMemoryStore(path)
    try:
        assert store.schema_version() == SCHEMA_VERSION == 20
        columns = {
            row[1]
            for row in store._db.execute("PRAGMA table_info(self_commitments)")
        }
        assert "next_attempt_at" in columns
        assert "lease_until" in columns
        migrated = store.get_self_commitment("aemeath", "legacy-capability")
        assert migrated.fulfillment_mode == "capability"
        assert migrated.next_attempt_at == 200
    finally:
        store.close()
    assert list(tmp_path.glob("legacy-v17.db.pre-migrate-v17-to-v20.*"))
