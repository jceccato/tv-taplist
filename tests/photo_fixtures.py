"""Small real images for the photo tests, generated with Pillow.

Shared by the photo library suite, the HTTP/board tests and the browser tests,
so they all describe a "photo" the same way. The one binary fixture is a HEIC
(`fixtures/sideways.heic`): nothing in CI or the image can encode HEVC (ADR-0009).
A camera RAW is generated here as a minimal linear DNG, which LibRaw reads.
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
                          ".webp": "WEBP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF",
                          ".bmp": "BMP", ".avif": "AVIF"}[path.suffix.lower()], **save_kwargs)
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


# ---- HEIC and camera RAW --------------------------------------------------------------

# A 120 x 80 landscape, left half red, right half blue (`halves`), saved by
# pillow-heif with EXIF orientation 6 (turn 90 degrees clockwise), a date taken of
# 2025:03:04 05:06:07 and a GPS fix. pillow-heif stores the turn as the HEIF
# rotation, so it reads back as an upright 80 x 120 portrait: red on top, blue
# below. Made with:
#   im.save("sideways.heic", "HEIF", quality=90,
#           exif=exif_bytes(orientation=6, taken="2025:03:04 05:06:07", gps=True))
HEIC_FIXTURE = Path(__file__).parent / "fixtures" / "sideways.heic"


def _ifd(entries: list[tuple[int, int, int, bytes]], offset: int) -> bytes:
    """One little-endian TIFF IFD at `offset`, with its out-of-line values after it."""
    entries = sorted(entries)
    data_at = offset + 2 + 12 * len(entries) + 4
    table, data = struct.pack("<H", len(entries)), b""
    for tag, typ, count, value in entries:
        if len(value) <= 4:
            table += struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\0")
        else:
            table += struct.pack("<HHII", tag, typ, count, data_at + len(data))
            data += value + b"\0" * (len(value) % 2)
    return table + struct.pack("<I", 0) + data


def _short(*v): return (3, len(v), struct.pack(f"<{len(v)}H", *v))
def _long(*v): return (4, len(v), struct.pack(f"<{len(v)}I", *v))
def _byte(*v): return (1, len(v), bytes(v))
def _text(s): b = s.encode() + b"\0"; return (2, len(b), b)
def _srational(*v): return (10, len(v), b"".join(struct.pack("<ii", round(x * 10000), 10000) for x in v))


def dng_bytes(raw: Image.Image, preview: Image.Image | None = None,
              orientation: int = 1, preview_exif: bytes | None = None,
              taken: str | None = None) -> bytes:
    """A minimal linear (already demosaiced) DNG of `raw`, which LibRaw reads.

    With `preview`, IFD 0 carries it as the camera's embedded JPEG preview (with
    `preview_exif` inside it, if given) and the RAW sits in a SubIFD, the way
    cameras lay a DNG out. `orientation` is the DNG's own (TIFF) orientation tag,
    `taken` its DateTime ('YYYY:MM:DD HH:MM:SS').
    """
    w, h = raw.size
    pixels = b"".join(struct.pack("<HHH", *(c * 257 for c in px))
                      for px in raw.convert("RGB").get_flattened_data())
    jpeg = b""
    if preview is not None:
        buf = io.BytesIO()
        preview.save(buf, "JPEG", quality=85, **({"exif": preview_exif} if preview_exif else {}))
        jpeg = buf.getvalue()

    def layout(ifd0_len: int, sub_len: int) -> tuple[bytes, bytes]:
        sub_at = 8 + ifd0_len
        jpeg_at = sub_at + sub_len
        raw_at = jpeg_at + len(jpeg)
        ifd0 = [
            (254, *_long(1 if preview is not None else 0)),
            (271, *_text("Test")), (272, *_text("Fixture")), (274, *_short(orientation)),
            (50706, *_byte(1, 4, 0, 0)), (50707, *_byte(1, 1, 0, 0)),
            (50708, *_text("Test Fixture")),
            (50721, *_srational(1, 0, 0, 0, 1, 0, 0, 0, 1)),      # ColorMatrix1: identity
            (50778, *_short(21)),                                  # D65
            (330, *_long(sub_at)),                                 # SubIFDs
        ]
        if taken:
            ifd0.append((306, *_text(taken)))
        if preview is not None:
            pw, ph = preview.size
            ifd0 += [(256, *_long(pw)), (257, *_long(ph)), (258, *_short(8, 8, 8)),
                     (259, *_short(7)), (262, *_short(6)), (277, *_short(3)),
                     (273, *_long(jpeg_at)), (278, *_long(ph)), (279, *_long(len(jpeg)))]
        sub = [(254, *_long(0)), (256, *_long(w)), (257, *_long(h)),
               (258, *_short(16, 16, 16)), (259, *_short(1)), (262, *_short(34892)),
               (277, *_short(3)), (273, *_long(raw_at)), (278, *_long(h)),
               (279, *_long(len(pixels))), (284, *_short(1))]
        return _ifd(ifd0, 8), _ifd(sub, sub_at)

    a, b = layout(0, 0)
    for _ in range(3):             # offsets depend on the IFD lengths; settle them
        a, b = layout(len(a), len(b))
    return b"II*\0" + struct.pack("<I", 8) + a + b + jpeg + pixels


def write_bytes(path: Path, data: bytes, age_seconds: float = 60.0) -> Path:
    """Write a file as an operator would drop it in (settled, so a scan takes it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    backdate(path, age_seconds)
    return path
