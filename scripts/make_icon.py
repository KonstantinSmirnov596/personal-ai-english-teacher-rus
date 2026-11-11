"""Draws assets/trainer.ico (blue rounded square with a white microphone) without extra
dependencies. Run once; the icon is committed.

    .venv\\Scripts\\python.exe scripts\\make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

ACCENT = (0x2F, 0x54, 0xC8)
WHITE = (0xFF, 0xFF, 0xFF)
SS = 4  # supersampling factor


def _inside_round_rect(
    x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float
) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _shape(x: float, y: float) -> tuple[int, int, int] | None:
    """Colour at a point of the unit square, or None for transparent."""
    if not _inside_round_rect(x, y, 0.0, 0.0, 1.0, 1.0, 0.22):
        return None
    # microphone capsule
    if _inside_round_rect(x, y, 0.40, 0.17, 0.60, 0.57, 0.10):
        return WHITE
    # holder arc: a ring segment below the capsule
    dx, dy = x - 0.5, y - 0.46
    dist = (dx * dx + dy * dy) ** 0.5
    if 0.185 <= dist <= 0.245 and dy >= 0.0:
        return WHITE
    # stand and base
    if 0.47 <= x <= 0.53 and 0.69 <= y <= 0.82:
        return WHITE
    if _inside_round_rect(x, y, 0.36, 0.79, 0.64, 0.85, 0.03):
        return WHITE
    return ACCENT


def render(size: int) -> bytes:
    """BGRA pixels, top-down."""
    out = bytearray()
    n = SS * SS
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    c = _shape((px + (sx + 0.5) / SS) / size, (py + (sy + 0.5) / SS) / size)
                    if c is not None:
                        r += c[0]
                        g += c[1]
                        b += c[2]
                        a += 1
            if a == 0:
                out += b"\x00\x00\x00\x00"
            else:
                out += bytes((b // a, g // a, r // a, 255 * a // n))
    return bytes(out)


def bmp_entry(size: int) -> bytes:
    pixels = render(size)
    rows = [pixels[i * size * 4 : (i + 1) * size * 4] for i in range(size)]
    xor = b"".join(reversed(rows))  # bottom-up
    mask_row = (size + 31) // 32 * 4
    and_mask = b"\x00" * (mask_row * size)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(xor), 0, 0, 0, 0)
    return header + xor + and_mask


def main() -> int:
    sizes = (16, 24, 32, 48, 64, 128)
    images = [bmp_entry(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for size, data in zip(sizes, images, strict=True):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path = Path(__file__).resolve().parent.parent / "assets" / "trainer.ico"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out + b"".join(images))
    print(path, len(out) + sum(len(i) for i in images), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
