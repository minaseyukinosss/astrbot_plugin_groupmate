from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from groupmate.social_runtime.persistence.schema import initialize_database
from groupmate.social_runtime.stickers.cognition import meaning_is_sendable
from groupmate.social_runtime.stickers.contracts import CANDIDATE_POOL_LIMIT
from groupmate.social_runtime.stickers.lexicon import StickerLexicon


def png_bytes(width: int = 16, height: int = 16) -> bytes:
    raw = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def gif_bytes() -> bytes:
    return (
        b"GIF89a"
        + struct.pack("<HH", 16, 16)
        + b"\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00"
        + struct.pack("<HH", 16, 16)
        + b"\x00\x02\x02\x4c\x01\x00;"
    )


def palette_gif(frame_indices: list[int], *, width: int = 8, height: int = 8) -> bytes:
    palette = [bytes((30 * index, 20, 255 - 20 * index)) for index in range(8)]
    payload = bytearray(b"GIF89a")
    payload.extend(struct.pack("<HH", width, height))
    payload.extend(b"\x82\x00\x00")
    for color in palette:
        payload.extend(color)
    min_code = 3
    pixels = width * height
    for color_index in frame_indices:
        payload.extend(b"\x21\xf9\x04\x00\x0a\x00\x00\x00,\x00\x00\x00\x00")
        payload.extend(struct.pack("<HH", width, height))
        payload.append(0)
        payload.append(min_code)
        payload.extend(_gif_subblocks(_lzw_encode([color_index] * pixels, min_code)))
    payload.append(0x3B)
    return bytes(payload)


def _lzw_encode(indices: list[int], min_code_size: int) -> bytes:
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    next_code = end + 1
    table = {bytes([index]): index for index in range(clear)}
    output = bytearray()
    buffer = 0
    bits = 0

    def emit(code: int, size: int) -> None:
        nonlocal buffer, bits
        buffer |= code << bits
        bits += size
        while bits >= 8:
            output.append(buffer & 0xFF)
            buffer >>= 8
            bits -= 8

    emit(clear, code_size)
    previous = bytes([indices[0]])
    for value in indices[1:]:
        candidate = previous + bytes([value])
        if candidate in table:
            previous = candidate
            continue
        emit(table[previous], code_size)
        if next_code < 4096:
            table[candidate] = next_code
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            emit(clear, code_size)
            table = {bytes([index]): index for index in range(clear)}
            code_size = min_code_size + 1
            next_code = end + 1
        previous = bytes([value])
    emit(table[previous], code_size)
    emit(end, code_size)
    if bits:
        output.append(buffer & 0xFF)
    return bytes(output)


def _gif_subblocks(data: bytes) -> bytes:
    packed = bytearray()
    cursor = 0
    while cursor < len(data):
        chunk = data[cursor : cursor + 255]
        packed.append(len(chunk))
        packed.extend(chunk)
        cursor += 255
    packed.append(0)
    return bytes(packed)


def jpeg_with_size(width: int, height: int) -> bytes:
    sof_payload = (
        b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    sof = b"\xff\xc0" + struct.pack(">H", len(sof_payload) + 2) + sof_payload
    dqt_payload = b"\x00" + (b"\x00" * 64)
    dqt = b"\xff\xdb" + struct.pack(">H", len(dqt_payload) + 2) + dqt_payload
    return b"\xff\xd8" + dqt + sof + b"\xff\xd9"


def lexicon(tmp_path: Path) -> StickerLexicon:
    database = tmp_path / "groupmate-social-runtime-v2.db"
    initialize_database(database)
    return StickerLexicon(tmp_path, database)


def test_generic_captions_are_not_sendable():
    assert meaning_is_sendable("摊手无奈，带一点我也没办法")
    assert not meaning_is_sendable("")
    assert not meaning_is_sendable("可爱")
    assert not meaning_is_sendable("表情包")
    assert not meaning_is_sendable("有趣")


def test_ingest_stores_candidate_under_hash_identity(tmp_path):
    store = lexicon(tmp_path)
    content = png_bytes()

    first = store.ingest(
        content,
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=100,
        skip_coarse_filter=True,
    )
    second = store.ingest(
        content,
        mime_type="image/png",
        origin_kind="group_captured",
        license_status="group_captured",
        now=120,
        source_group_id="g2",
        source_event_id="evt-2",
    )

    assert first.created is True
    assert first.card is not None
    assert first.card.status == "candidate"
    assert first.card.asset_id == f"sticker:{hashlib.sha256(content).hexdigest()[:24]}"
    stored = tmp_path / first.card.relative_path
    assert stored.read_bytes() == content
    assert stored.is_relative_to(tmp_path.resolve())
    assert second.created is False
    assert second.reason == "seen_before"
    assert second.card is not None
    assert second.card.sighting_count == 2
    assert second.card.source_group_id is None


def test_candidate_and_empty_meaning_cannot_become_ready(tmp_path):
    store = lexicon(tmp_path)
    outcome = store.ingest(
        png_bytes(),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    )
    card = outcome.card
    assert card is not None
    assert store.ready_sendable() == ()

    store.write_cognition(card.asset_id, meaning="可爱", now=2)
    try:
        store.confirm(card.asset_id, now=3)
        raise AssertionError("generic meaning must not confirm")
    except Exception as exc:
        assert "meaning" in str(exc)

    store.write_cognition(card.asset_id, meaning="摊手无奈，我也没办法", now=4)
    ready = store.confirm(card.asset_id, now=5)
    assert ready.status == "ready"
    assert ready.sendable()


def test_rejected_hash_never_reenters(tmp_path):
    store = lexicon(tmp_path)
    content = png_bytes()
    created = store.ingest(
        content,
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    )
    store.reject(created.card.asset_id, now=2)
    again = store.ingest(
        content,
        mime_type="image/png",
        origin_kind="group_captured",
        license_status="group_captured",
        now=3,
    )
    assert again.card is None
    assert again.reason == "rejected_hash"


def test_candidate_pool_stops_new_cards_without_deleting(tmp_path):
    store = lexicon(tmp_path)
    kept = []
    for index in range(CANDIDATE_POOL_LIMIT):
        content = png_bytes(width=16 + index, height=16)
        outcome = store.ingest(
            content,
            mime_type="image/png",
            origin_kind="group_captured",
            license_status="group_captured",
            now=index + 1,
            skip_coarse_filter=True,
        )
        assert outcome.created is True
        kept.append(outcome.card.asset_id)
    overflow = store.ingest(
        png_bytes(width=80, height=16),
        mime_type="image/png",
        origin_kind="group_captured",
        license_status="group_captured",
        now=99,
        skip_coarse_filter=True,
    )
    assert overflow.created is False
    assert overflow.reason == "candidate_pool_full"
    assert store.capacity()["candidate_full"] is True
    assert {card.asset_id for card in store.inbox()} == set(kept)


def test_affection_floor_snaps_to_public_relationship_stages(tmp_path):
    from groupmate.social_runtime.stickers.contracts import (
        affection_floor_label,
        parse_affection_floor,
        snap_affection_floor,
    )

    store = lexicon(tmp_path)
    card = store.ingest(
        png_bytes(),
        mime_type="image/png",
        origin_kind="admin_import",
        license_status="owned",
        now=1,
        skip_coarse_filter=True,
    ).card
    saved = store.write_cognition(
        card.asset_id, meaning="摊手无奈我也没办法", min_familiarity=40, now=2
    )
    public = store.public_card(saved)
    assert snap_affection_floor(40) == 30
    assert parse_affection_floor("至少亲近") == 55
    assert parse_affection_floor("familiar") == 30
    assert saved.min_familiarity == 30
    assert public["affection_floor_label"] == "至少熟悉"
    assert affection_floor_label(0) == "谁都行"
    assert affection_floor_label(80) == "仅默契"
