"""Draw client/shell/icon.png.

Standard library only -- zlib and struct write the PNG, and the shape is a
signed-distance field evaluated per pixel. That is more arithmetic than calling
an image library, and it is the reason there is no image library in this repo's
requirements for the sake of one 512x512 file that changes approximately never.

Run it after editing the constants below:

    ./venv/Scripts/python.exe client/shell/make-icon.py
"""

import math
import struct
import zlib
from pathlib import Path

SIZE = 512
BG = (0x16, 0x18, 0x26)
FG = (0x91, 0x84, 0xD9)

# The two stacked drives, in the 200-unit space the prototype's thumbnail used,
# scaled up here. Keeping the original numbers means the icon and that thumbnail
# stay the same drawing rather than two drawings that resemble each other.
SCALE = SIZE / 200
STROKE = 7 * SCALE
FEATHER = 1.4  # pixels of anti-aliasing on each edge

BAYS = [
    # centre x, centre y, half width, half height, corner radius (200-space)
    (100, 81, 38, 15, 9),
    (100, 121, 38, 15, 9),
]
LEDS = [(79, 81, 3.2), (79, 121, 3.2)]


def rounded_rect_distance(px, py, cx, cy, hw, hh, radius):
    """Signed distance from (px, py) to a rounded rectangle's edge."""
    dx = abs(px - cx) - (hw - radius)
    dy = abs(py - cy) - (hh - radius)
    outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
    inside = min(max(dx, dy), 0.0)
    return outside + inside - radius


def coverage(distance, half_width):
    """How much of this pixel the stroke covers, 0..1, smoothed over FEATHER."""
    edge = abs(distance) - half_width
    if edge <= -FEATHER:
        return 1.0
    if edge >= FEATHER:
        return 0.0
    return (FEATHER - edge) / (2 * FEATHER)


def build():
    """Everything below is in pixels.

    The shape constants are authored in the 200-unit space the thumbnail used,
    so they are converted once, here. Mixing the two spaces inside the pixel
    loop is how a stroke width ends up scaled twice.
    """
    bays = [(cx * SCALE, cy * SCALE, hw * SCALE, hh * SCALE, r * SCALE)
            for cx, cy, hw, hh, r in BAYS]
    leds = [(lx * SCALE, ly * SCALE, lr * SCALE) for lx, ly, lr in LEDS]
    half_stroke = STROKE / 2

    rows = []
    for y in range(SIZE):
        row = bytearray()
        py = y + 0.5
        for x in range(SIZE):
            px = x + 0.5
            alpha = 0.0
            for cx, cy, hw, hh, radius in bays:
                distance = rounded_rect_distance(px, py, cx, cy, hw, hh, radius)
                alpha = max(alpha, coverage(distance, half_stroke))
            for lx, ly, lr in leds:
                # Filled, not stroked: the distance is measured from the centre
                # and the "half width" is the radius itself.
                alpha = max(alpha, coverage(math.hypot(px - lx, py - ly) - lr, lr))
            alpha = min(1.0, alpha)
            row += bytes(round(BG[i] + (FG[i] - BG[i]) * alpha) for i in range(3))
            row += b"\xff"
        rows.append(row)

    return b"".join(b"\x00" + bytes(row) for row in rows)


def chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def main():
    raw = build()
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    out = Path(__file__).with_name("icon.png")
    out.write_bytes(png)
    print(f"wrote {out} ({len(png):,} bytes, {SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
