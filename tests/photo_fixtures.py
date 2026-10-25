"""Small real images for the photo tests, generated with Pillow (no binary fixtures).

Shared by the photo library suite, the HTTP/board tests and the browser tests,
so they all describe a "photo" the same way.
"""
from __future__ import annotations

import io
import os
import struct
import time
from pathlib import Path

from PIL import Image

# EXIF tags used to build the awkward cases.
ORIENTATION = 0x0112
DATETIME = 0x0132
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
DATETIME_ORIGINAL = 0x9003


def exif_bytes(orientation: int | None = None, taken: str | None = None,
               gps: bool = False) -> bytes:
    """An EXIF block with the given orientation, date taken and (optionally) a GPS fix."""
    exif = Image.Exif()
    if orientation is not None:
        exif[ORIENTATION] = orientation
    if taken is not None:
        exif.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = taken
    if gps:
        gps_ifd = exif.get_ifd(GPS_IFD)
        gps_ifd[1] = "S"                               # latitude ref
        gps_ifd[2] = (37.0, 48.0, 29.0)                 # latitude
        gps_ifd[3] = "E"                               # longitude ref
        gps_ifd[4] = (145.0, 8.0, 55.0)                 # longitude
    return exif.tobytes()


def p3_profile() -> bytes:
    """A minimal matrix/TRC ICC profile with Display P3 primaries (D50 adapted)."""
    def s15(v: float) -> bytes:
        return struct.pack(">i", round(v * 65536))

    def xyz(x: float, y: float, z: float) -> bytes:
        return b"XYZ \x00\x00\x00\x00" + s15(x) + s15(y) + s15(z)

    text = b"desc\x00\x00\x00\x00" + struct.pack(">I", 11) + b"Display P3\x00" + b"\x00" * 79
    curve = b"curv\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">H", 0x0233) + b"\x00\x00"
    tags = [
        (b"desc", text),
        (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
        (b"rXYZ", xyz(0.5151, 0.2412, -0.0011)),
        (b"gXYZ", xyz(0.2920, 0.6922, 0.0419)),
        (b"bXYZ", xyz(0.1571, 0.0666, 0.7841)),
        (b"rTRC", curve), (b"gTRC", curve), (b"bTRC", curve),
        (b"cprt", b"text\x00\x00\x00\x00test\x00"),
    ]
    table_len = 4 + 12 * len(tags)
    offset = 128 + table_len
    table, body = b"", b""
    for sig, data in tags:
        pad = (-len(data)) % 4
        table += sig + struct.pack(">II", offset + len(body), len(data))
        body += data + b"\x00" * pad
    total = 128 + table_len + len(body)
    header = struct.pack(">I", total) + b"\x00\x00\x00\x00" + b"\x02\x10\x00\x00" + b"mntr" + b"RGB " + b"XYZ "
    header += b"\x00" * 12 + b"acsp" + b"\x00" * 28
    header += s15(0.9642) + s15(1.0) + s15(0.8249)
    header = header.ljust(128, b"\x00")
    return header + struct.pack(">I", len(tags)) + table + body


def halves(size=(400, 200), left=(220, 20, 20), right=(20, 20, 220)) -> Image.Image:
    """A landscape image, left half one colour, right half another."""
    im = Image.new("RGB", size, left)
    im.paste(Image.new("RGB", (size[0] - size[0] // 2, size[1]), right), (size[0] // 2, 0))
    return im


def write_image(path: Path, im: Image.Image, fmt: str | None = None, *,
                age_seconds: float = 60.0, **save_kwargs) -> Path:
    """Save an image and backdate it, so a scan sees it as settled (not still copying in)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, fmt or {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                          ".webp": "WEBP"}[path.suffix.lower()], **save_kwargs)
    backdate(path, age_seconds)
    return path


def backdate(path: Path, age_seconds: float) -> None:
    t = time.time() - age_seconds
    os.utime(path, (t, t))


def png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def riff_chunks(data: bytes) -> list[str]:
    """The four-character chunk names of a RIFF/WebP file."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    chunks, pos = [], 12
    while pos + 8 <= len(data):
        chunks.append(data[pos:pos + 4].decode("latin-1"))
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        pos += 8 + size + (size & 1)
    return chunks
