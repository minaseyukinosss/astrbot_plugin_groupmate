"""MIME magic, still-image coarse filters, and GIF vision sheets. Not a visual model."""

from __future__ import annotations

import base64
import struct
import zlib


MIME_RULES = {
    "image/png": ((".png",), lambda content: content.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ((".jpg", ".jpeg"), lambda content: content.startswith(b"\xff\xd8\xff")),
    "image/gif": ((".gif",), lambda content: content.startswith((b"GIF87a", b"GIF89a"))),
    "image/webp": (
        (".webp",),
        lambda content: len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP",
    ),
}

_STILL_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_MAX_STILL_EDGE = 800
_MAX_STILL_PIXELS = 480_000
_MAX_STILL_BYTES = 350_000
VISION_GIF_FRAME_LIMIT = 6
_MAX_GIF_FRAMES_DECODED = 48
_VISION_GIF_MAX_HEIGHT = 192
_VISION_GIF_MAX_SHEET_WIDTH = 1536
_MAX_GIF_CANVAS_PIXELS = 480_000


def extension_for(mime_type: str) -> str:
    rule = MIME_RULES.get(mime_type)
    if rule is None:
        raise ValueError("unsupported sticker MIME")
    return rule[0][0]


def mime_matches(content: bytes, mime_type: str) -> bool:
    rule = MIME_RULES.get(mime_type)
    return bool(rule and rule[1](content))


def sniff_mime(content: bytes) -> str:
    for mime_type, rule in MIME_RULES.items():
        if rule[1](content):
            return mime_type
    return ""


def looks_like_sticker_file(content: bytes, mime_type: str) -> bool:
    """Cheap inbound sieve: GIFs pass; oversized stills do not."""

    if mime_type == "image/gif":
        return True
    if mime_type not in _STILL_MIMES:
        return False
    if len(content) > _MAX_STILL_BYTES:
        return False
    extent = image_extent(content, mime_type)
    if extent is None:
        return mime_type != "image/jpeg" or len(content) <= 80_000
    width, height = extent
    if width > _MAX_STILL_EDGE or height > _MAX_STILL_EDGE:
        return False
    return (width * height) <= _MAX_STILL_PIXELS


def image_extent(content: bytes, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png":
        return _png_extent(content)
    if mime_type == "image/jpeg":
        return _jpeg_extent(content)
    if mime_type == "image/gif" and len(content) >= 10:
        return int.from_bytes(content[6:8], "little"), int.from_bytes(
            content[8:10], "little"
        )
    if mime_type == "image/webp":
        return _webp_extent(content)
    return None


def _png_extent(content: bytes) -> tuple[int, int] | None:
    if len(content) < 24 or content[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", content[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def _jpeg_extent(content: bytes) -> tuple[int, int] | None:
    index = 2
    length = len(content)
    while index + 9 < length:
        if content[index] != 0xFF:
            return None
        marker = content[index + 1]
        if marker in {0xD8, 0xD9}:
            index += 2
            continue
        if marker == 0x00 or marker == 0xFF:
            index += 1
            continue
        if index + 4 > length:
            return None
        size = int.from_bytes(content[index + 2 : index + 4], "big")
        if size < 2:
            return None
        if marker in {0xC0, 0xC1, 0xC2} and index + 9 <= length:
            height = int.from_bytes(content[index + 5 : index + 7], "big")
            width = int.from_bytes(content[index + 7 : index + 9], "big")
            if width > 0 and height > 0:
                return width, height
        index += 2 + size
    return None


def _webp_extent(content: bytes) -> tuple[int, int] | None:
    if len(content) < 30:
        return None
    kind = content[12:16]
    if kind == b"VP8X" and len(content) >= 30:
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if kind == b"VP8 " and len(content) >= 30:
        width = int.from_bytes(content[26:28], "little") & 0x3FFF
        height = int.from_bytes(content[28:30], "little") & 0x3FFF
        return width, height
    return None


def representative_frame(content: bytes, mime_type: str) -> tuple[bytes, str]:
    """Vision input only. GIFs become a 4–6 frame sheet; preview/send keep the original."""

    payload = bytes(content or b"")
    mime = sniff_mime(payload) or str(mime_type or "").strip().casefold()
    if mime != "image/gif":
        return payload, mime or str(mime_type or "").strip().casefold()
    try:
        return _gif_vision_png(payload), "image/png"
    except (ValueError, struct.error, IndexError, OverflowError, MemoryError):
        return payload, "image/gif"


def vision_data_uri(content: bytes, mime_type: str) -> str:
    frame, mime = representative_frame(content, mime_type)
    encoded = base64.b64encode(frame).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def sample_gif_frame_indices(count: int, limit: int = VISION_GIF_FRAME_LIMIT) -> tuple[int, ...]:
    total = int(count)
    cap = max(1, int(limit))
    if total <= 0:
        return ()
    if total <= cap:
        return tuple(range(total))
    return tuple(index * (total - 1) // (cap - 1) for index in range(cap))


def _gif_vision_png(content: bytes) -> bytes:
    width, height, frames = _gif_composited_frames(content)
    chosen = [frames[index] for index in sample_gif_frame_indices(len(frames))]
    if not chosen:
        raise ValueError("GIF has no drawable frames")
    cell_w, cell_h = _vision_cell_size(width, height, len(chosen))
    cells = [_scale_rgba(frame, width, height, cell_w, cell_h) for frame in chosen]
    if len(cells) == 1:
        return _rgba_png(cell_w, cell_h, cells[0])
    return _stitch_rgba(cells, cell_w, cell_h)


def _vision_cell_size(width: int, height: int, count: int) -> tuple[int, int]:
    target_h = height if height <= _VISION_GIF_MAX_HEIGHT else _VISION_GIF_MAX_HEIGHT
    target_w = max(1, int(width * target_h / height))
    max_cell = max(1, _VISION_GIF_MAX_SHEET_WIDTH // max(1, count))
    if target_w > max_cell:
        target_w = max_cell
        target_h = max(1, int(height * target_w / width))
    return target_w, target_h


def _gif_composited_frames(content: bytes) -> tuple[int, int, list[bytes]]:
    if len(content) < 13 or content[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("not a GIF")
    width = int.from_bytes(content[6:8], "little")
    height = int.from_bytes(content[8:10], "little")
    if width <= 0 or height <= 0 or width * height > _MAX_GIF_CANVAS_PIXELS:
        raise ValueError("GIF canvas is too large for vision")
    packed = content[10]
    background = content[11]
    index = 13
    gct = _read_color_table(content, index, packed)
    if packed & 0x80:
        index += (2 << (packed & 0x07)) * 3
    bg = gct[background] if background < len(gct) else (gct[0] if gct else b"\x00\x00\x00")
    canvas = _fill_rgba(width, height, bg)
    previous: bytearray | None = None
    disposal = 0
    transparent: int | None = None
    frames: list[bytes] = []
    while index < len(content) and len(frames) < _MAX_GIF_FRAMES_DECODED:
        marker = content[index]
        index += 1
        if marker == 0x3B:
            break
        if marker == 0x21:
            if index >= len(content):
                break
            label = content[index]
            index += 1
            if label == 0xF9:
                disposal, transparent, index = _read_graphic_control(content, index)
            else:
                index = _skip_subblocks(content, index)
            continue
        if marker != 0x2C:
            continue
        if index + 9 > len(content):
            raise ValueError("GIF image descriptor is truncated")
        left = int.from_bytes(content[index : index + 2], "little")
        top = int.from_bytes(content[index + 2 : index + 4], "little")
        frame_w = int.from_bytes(content[index + 4 : index + 6], "little")
        frame_h = int.from_bytes(content[index + 6 : index + 8], "little")
        field = content[index + 8]
        index += 9
        if field & 0x40:
            raise ValueError("interlaced GIF")
        table = gct
        if field & 0x80:
            table = _read_color_table(content, index, field)
            index += (2 << (field & 0x07)) * 3
        if not table:
            raise ValueError("GIF has no color table")
        if index >= len(content):
            raise ValueError("GIF image data is truncated")
        min_code = content[index]
        index += 1
        data, index = _read_subblock_bytes(content, index)
        codes = _gif_lzw_decode(min_code, data, frame_w * frame_h)
        if disposal == 3:
            previous = bytearray(canvas)
        _blit_gif_frame(
            canvas,
            width,
            height,
            codes,
            table,
            left=left,
            top=top,
            frame_w=frame_w,
            frame_h=frame_h,
            transparent=transparent,
        )
        frames.append(bytes(canvas))
        if disposal == 2:
            _fill_rect(canvas, width, height, (left, top, frame_w, frame_h), bg)
        elif disposal == 3 and previous is not None:
            canvas = previous
        disposal = 0
        transparent = None
    if not frames:
        raise ValueError("GIF has no drawable frames")
    return width, height, frames


def _read_color_table(content: bytes, index: int, packed: int) -> list[bytes]:
    if not packed & 0x80:
        return []
    count = 2 << (packed & 0x07)
    needed = index + count * 3
    if needed > len(content):
        raise ValueError("GIF color table is truncated")
    return [content[index + offset : index + offset + 3] for offset in range(0, count * 3, 3)]


def _read_graphic_control(content: bytes, index: int) -> tuple[int, int | None, int]:
    if index >= len(content):
        return 0, None, index
    size = content[index]
    index += 1
    if size < 4 or index + size > len(content):
        return 0, None, _skip_subblocks(content, index)
    fields = content[index]
    trans_index = content[index + 3]
    index += size
    if index < len(content) and content[index] == 0:
        index += 1
    else:
        index = _skip_subblocks(content, index)
    disposal = (fields >> 2) & 0x07
    transparent = trans_index if fields & 0x01 else None
    return disposal, transparent, index


def _skip_subblocks(content: bytes, index: int) -> int:
    while index < len(content):
        block = content[index]
        index += 1
        if block == 0:
            break
        index += block
    return index


def _read_subblock_bytes(content: bytes, index: int) -> tuple[bytes, int]:
    data = bytearray()
    while index < len(content):
        block = content[index]
        index += 1
        if block == 0:
            break
        data.extend(content[index : index + block])
        index += block
    return bytes(data), index


def _fill_rgba(width: int, height: int, rgb: bytes) -> bytearray:
    pixel = bytes(rgb[:3]) + b"\xff"
    canvas = bytearray(pixel * (width * height))
    return canvas


def _fill_rect(
    canvas: bytearray,
    width: int,
    height: int,
    rect: tuple[int, int, int, int],
    rgb: bytes,
) -> None:
    left, top, frame_w, frame_h = rect
    pixel = bytes(rgb[:3]) + b"\xff"
    for row in range(frame_h):
        py = top + row
        if py < 0 or py >= height:
            continue
        for column in range(frame_w):
            px = left + column
            if px < 0 or px >= width:
                continue
            offset = (py * width + px) * 4
            canvas[offset : offset + 4] = pixel


def _blit_gif_frame(
    canvas: bytearray,
    width: int,
    height: int,
    codes: list[int],
    table: list[bytes],
    *,
    left: int,
    top: int,
    frame_w: int,
    frame_h: int,
    transparent: int | None,
) -> None:
    fallback = table[0] if table else b"\x00\x00\x00"
    for row in range(frame_h):
        py = top + row
        if py < 0 or py >= height:
            continue
        base = row * frame_w
        for column in range(frame_w):
            px = left + column
            if px < 0 or px >= width:
                continue
            color_index = codes[base + column] if base + column < len(codes) else 0
            if transparent is not None and color_index == transparent:
                continue
            color = table[color_index] if color_index < len(table) else fallback
            offset = (py * width + px) * 4
            canvas[offset : offset + 3] = color
            canvas[offset + 3] = 255


def _scale_rgba(pixels: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    if src_w == dst_w and src_h == dst_h:
        return pixels
    out = bytearray(dst_w * dst_h * 4)
    for row in range(dst_h):
        src_row = row * src_h // dst_h
        for column in range(dst_w):
            src_col = column * src_w // dst_w
            src = (src_row * src_w + src_col) * 4
            dst = (row * dst_w + column) * 4
            out[dst : dst + 4] = pixels[src : src + 4]
    return bytes(out)


def _stitch_rgba(cells: list[bytes], cell_w: int, cell_h: int) -> bytes:
    count = len(cells)
    width = cell_w * count
    out = bytearray(width * cell_h * 4)
    row_bytes = cell_w * 4
    for index, cell in enumerate(cells):
        origin = index * cell_w
        for row in range(cell_h):
            src = row * row_bytes
            dst = (row * width + origin) * 4
            out[dst : dst + row_bytes] = cell[src : src + row_bytes]
    return _rgba_png(width, cell_h, bytes(out))


def _gif_lzw_decode(min_code_size: int, data: bytes, limit: int) -> list[int]:
    if min_code_size < 2 or min_code_size > 8:
        raise ValueError("GIF LZW code size is invalid")
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    next_code = end + 1
    table = {index: [index] for index in range(clear)}
    output: list[int] = []
    previous: list[int] | None = None
    buffer = 0
    bits = 0
    cursor = 0

    def read_code() -> int | None:
        nonlocal buffer, bits, cursor, code_size
        while bits < code_size:
            if cursor >= len(data):
                return None
            buffer |= data[cursor] << bits
            cursor += 1
            bits += 8
        code = buffer & ((1 << code_size) - 1)
        buffer >>= code_size
        bits -= code_size
        return code

    while len(output) < limit:
        code = read_code()
        if code is None or code == end:
            break
        if code == clear:
            table = {index: [index] for index in range(clear)}
            code_size = min_code_size + 1
            next_code = end + 1
            previous = None
            continue
        if code in table:
            entry = table[code]
        elif previous is not None and code == next_code:
            entry = previous + [previous[0]]
        else:
            raise ValueError("GIF LZW stream is corrupt")
        output.extend(entry)
        if previous is not None and next_code < 4096:
            table[next_code] = previous + [entry[0]]
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        previous = entry
    return output[:limit]


def _rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise ValueError("PNG pixel buffer does not match extent")
    raw = b"".join(
        b"\x00" + pixels[row * width * 4 : (row + 1) * width * 4]
        for row in range(height)
    )
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


__all__ = (
    "MIME_RULES",
    "VISION_GIF_FRAME_LIMIT",
    "extension_for",
    "image_extent",
    "looks_like_sticker_file",
    "mime_matches",
    "representative_frame",
    "sample_gif_frame_indices",
    "sniff_mime",
    "vision_data_uri",
)
