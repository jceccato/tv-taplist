"""Cut a generated picture frame's opening out, for the framed print (issue #61).

The realistic frames are generated (Nano Banana) as a frame seen straight-on,
filling a 16:9 canvas, with its opening a flat #FF00FF magenta. The page draws
one with CSS `border-image`, which keeps the corners and stretches the sides,
so all this needs is the opening made transparent and the four side widths.

Plain colour keying leaves a magenta rim, because the model's edge pixels are a
blend of frame and key. So the opening is found as a rectangle (the rows and
columns that are mostly key), cut a few pixels wider than the key reaches, and
the band just outside it is despilled. The cut-out is then checked for any
pixel that still reads as magenta, and the tool prints PASS or FAIL.

    python scripts/frame_cutout.py cut in.jpg out.webp [--inset 3] [--width 1376]
        -> prints the side widths as JSON: {"w", "h", "top", "right", "bottom", "left", "check"}
    python scripts/frame_cutout.py sheet out.jpg photo.jpg A.webp B.webp ... [--labels A B ...]
        -> a contact sheet: each frame over the photo at 16:9

Run it with the project's Python (Pillow and numpy, both in requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageOps

KEY_DOMINANCE = 90       # min(R, B) - G above this is key
LEFTOVER_DOMINANCE = 60  # an opaque pixel above this still reads as magenta
TINT = 8                 # an edge line whose median is above this still carries the key's blend
RIM_TINT = 20            # near the opening, an opaque pixel above this shows as a pink-red line
MOSTLY = 0.5             # a row or column of the opening is at least this much key
WEBP_QUALITY = 90


def _dominance(rgb: np.ndarray) -> np.ndarray:
    return np.minimum(rgb[..., 0], rgb[..., 2]) - rgb[..., 1]


def _span(fractions: np.ndarray) -> tuple[int, int]:
    """The first and last index of the longest run of indices above MOSTLY."""
    best, start, run = (0, -1), None, 0
    for i, f in enumerate(list(fractions) + [0.0]):
        if f >= MOSTLY:
            start = i if start is None else start
        elif start is not None:
            if i - start > best[1] - best[0] + 1:
                best = (start, i - 1)
            start = None
    if best[1] < 0:
        raise SystemExit("no key-coloured opening found")
    return best


def cut(src: str, dst: str, inset: int = 3, width: int | None = None) -> dict:
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    if width and im.width > width:      # a frame is drawn only ~6 % of the screen wide
        im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
    rgb = np.asarray(im).astype(np.float64)
    h, w, _ = rgb.shape
    key = _dominance(rgb) > KEY_DOMINANCE
    y0, y1 = _span(key.mean(axis=1))
    x0, x1 = _span(key.mean(axis=0))
    # Grow each edge while the next line out is still tinted by the key: a soft
    # edge (a downscale blends it further) reaches past the mostly-key lines,
    # and despilling those pixels turns them red, not frame-coloured.
    dom = _dominance(rgb)

    def tinted(line: np.ndarray) -> bool:
        return line.size > 0 and float(np.median(line)) > TINT

    while x0 > 0 and tinted(dom[y0:y1 + 1, x0 - 1]):
        x0 -= 1
    while x1 < w - 1 and tinted(dom[y0:y1 + 1, x1 + 1]):
        x1 += 1
    while y0 > 0 and tinted(dom[y0 - 1, x0:x1 + 1]):
        y0 -= 1
    while y1 < h - 1 and tinted(dom[y1 + 1, x0:x1 + 1]):
        y1 += 1
    # Then wider still, so the blended rim goes with it.
    x0, y0, x1, y1 = max(0, x0 - inset), max(0, y0 - inset), min(w - 1, x1 + inset), min(h - 1, y1 + inset)
    alpha = np.full((h, w), 255, np.uint8)
    alpha[y0:y1 + 1, x0:x1 + 1] = 0
    # Despill a band outside the cut: take the magenta excess back out of R and B.
    band = np.zeros((h, w), bool)
    pad = inset * 3
    band[max(0, y0 - pad):y1 + pad + 1, max(0, x0 - pad):x1 + pad + 1] = True
    band &= alpha > 0
    excess = np.clip(_dominance(rgb), 0, None)
    for c in (0, 2):
        rgb[..., c] = np.where(band, rgb[..., c] - excess, rgb[..., c])
    # The opening is invisible, but WebP keeps colour at half resolution, so a
    # magenta left under it bleeds a pink-red line into the frame's first pixels.
    # Neutral grey bleeds nothing.
    rgb[alpha == 0] = 128
    after = _dominance(rgb)
    leftover = int(((after > LEFTOVER_DOMINANCE) & (alpha > 0)).sum())
    leftover += int(((after > RIM_TINT) & band).sum())    # the faint line the eye still sees
    out = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), alpha])
    Image.fromarray(out, "RGBA").save(dst, "WEBP", quality=WEBP_QUALITY, method=6)
    saved = np.asarray(Image.open(dst).convert("RGB")).astype(np.float64)
    leftover += int(((_dominance(saved) > RIM_TINT) & band).sum())   # as encoded
    result = {"w": w, "h": h, "top": y0, "right": w - 1 - x1, "bottom": h - 1 - y1, "left": x0,
              "check": "PASS" if leftover == 0 else f"FAIL ({leftover} magenta pixels left)"}
    return result


def sheet(dst: str, photo: str, frames: list[str], labels: list[str] | None = None) -> None:
    cw, ch = 640, 360
    cols = min(3, len(frames))
    rows = (len(frames) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * (cw + 10) + 10, rows * (ch + 34) + 10), (24, 24, 26))
    draw = ImageDraw.Draw(canvas)
    pic = ImageOps.fit(ImageOps.exif_transpose(Image.open(photo)).convert("RGB"), (cw, ch))
    for i, f in enumerate(frames):
        fr = Image.open(f).convert("RGBA").resize((cw, ch), Image.Resampling.LANCZOS)
        tile = pic.copy()
        tile.paste(fr, (0, 0), fr)
        x, y = 10 + (i % cols) * (cw + 10), 10 + (i // cols) * (ch + 34)
        canvas.paste(tile, (x, y))
        draw.text((x, y + ch + 6), labels[i] if labels else f, fill=(230, 230, 230))
    canvas.save(dst, quality=88)


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut")
    c.add_argument("src")
    c.add_argument("dst")
    c.add_argument("--inset", type=int, default=3)
    c.add_argument("--width", type=int, help="scale down to this width first")
    s = sub.add_parser("sheet")
    s.add_argument("dst")
    s.add_argument("photo")
    s.add_argument("frames", nargs="+")
    s.add_argument("--labels", nargs="*")
    a = ap.parse_args(argv)
    if a.cmd == "cut":
        print(json.dumps(cut(a.src, a.dst, a.inset, a.width)))
    else:
        sheet(a.dst, a.photo, a.frames, a.labels)


if __name__ == "__main__":
    main(sys.argv[1:])
