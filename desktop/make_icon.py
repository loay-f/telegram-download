"""Draws desktop/icon.ico (a blue tile with an open book) using PyMuPDF only."""

import struct
from pathlib import Path

import pymupdf

OUT = Path(__file__).with_name("icon.ico")
SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_png(size: int) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=256, height=256)
    shape = page.new_shape()
    # Rounded tile
    shape.draw_rect(pymupdf.Rect(8, 8, 248, 248), radius=0.22)
    shape.finish(fill=(0.13, 0.62, 0.85), color=None)
    # Open book: two pages meeting at the spine
    left = [(44, 78), (124, 92), (124, 196), (44, 182)]
    right = [(212, 78), (132, 92), (132, 196), (212, 182)]
    for poly in (left, right):
        shape.draw_polyline([pymupdf.Point(*p) for p in poly] + [pymupdf.Point(*poly[0])])
        shape.finish(fill=(1, 1, 1), color=(1, 1, 1), width=6, closePath=True, lineJoin=1)
    # Text lines on the pages
    for i in range(4):
        y = 112 + i * 18
        shape.draw_line((60, y - 2 + i), (110, y + 6 + i))
        shape.draw_line((196, y - 2 + i), (146, y + 6 + i))
    shape.finish(color=(0.13, 0.62, 0.85), width=5, lineCap=1)
    shape.commit()
    pix = page.get_pixmap(matrix=pymupdf.Matrix(size / 256, size / 256), alpha=True)
    # The page background is transparent only outside drawn shapes when alpha=True.
    return pix.tobytes("png")


def main() -> None:
    images = [draw_png(s) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    OUT.write_bytes(header + entries + b"".join(images))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
