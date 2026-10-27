import struct
import zlib
from pathlib import Path

SIZE = 128
RADIUS = 30


def rounded(x, y, w, h, r):
    dx = max(r - x, 0, x - (w - 1 - r))
    dy = max(r - y, 0, y - (h - 1 - r))
    return dx * dx + dy * dy <= r * r


def build():
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)  # filter type 0
        t = y / (SIZE - 1)
        r = int(99 + (139 - 99) * t)
        g = int(102 + (92 - 102) * t)
        b = int(241 + (246 - 241) * t)
        for x in range(SIZE):
            if rounded(x, y, SIZE, SIZE, RADIUS):
                # 中间挖一个白色虚线感的方点，做成简单的 "Q" 意象
                cx, cy = x - SIZE / 2, y - SIZE / 2
                ring = 26 <= (cx * cx + cy * cy) ** 0.5 <= 34
                tail = 6 <= x - y <= 14 and x > SIZE * 0.62 and y > SIZE * 0.62
                if ring or tail:
                    raw.extend((255, 255, 255, 255))
                else:
                    raw.extend((r, g, b, 255))
            else:
                raw.extend((0, 0, 0, 0))
    return bytes(raw)


def chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path, raw):
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(png)
    print("wrote", path, len(png), "bytes")


if __name__ == "__main__":
    write_png(Path(__file__).parent / "extension" / "icon128.png", build())
