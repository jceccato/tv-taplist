"""Make the crease textures for the photo overlays from CC0 photographs (issue #63).

A crease on an old print is a fold line: a light ridge beside a dark one, plus
the paper's own grain along it. This takes a CC0 photograph of folded or
crumpled paper, crops the part that holds the fold, and keeps only its fine
detail (the picture minus a heavy blur of itself), so the lighting and the
sheet's own shade fall away. The result is centred on neutral grey (128): the
page lays it over the print with `mix-blend-mode: overlay`, under which 128
changes nothing, so only the fold and the grain show.

    python scripts/crease_textures.py make in.jpg out.webp --crop x0 y0 x1 y1 [--size 1024] [--median 0] [--along x|y]
        -> prints {"w", "h", "spread"} (spread: the 1st-99th percentile range, a check
           that the fold survived; a flat crop prints a small number). --median N
           (odd, px) takes out specks of dust on the photographed sheet first.
           --along y (or x) smooths the detail along a fold that runs that way, so
           a speck averages away and the line stays.
    python scripts/crease_textures.py sheet out.jpg photo.jpg A.webp B.webp ...
        -> a contact sheet: each texture over the photo, as the page blends it

Sources and crops are recorded in static/creases/README.md. Run it with the
project's Python (Pillow and numpy, both in requirements.txt).
"""
from __future__ import annotations

import argparse
import json

import numpy as np
from PIL import Image, ImageFilter, ImageOps

BLUR = 0.04           # the blur removed, as a fraction of the crop's short side
DENOISE = 2.0         # px: camera noise smoothed before the fold is kept
CORE = 1.5            # detail under this many times its median size is paper grain and noise:
                      # it is cut back by that much, so the fold carries the texture
PEAK = 0.995          # this percentile of |detail| maps to REACH
REACH = 100           # grey levels either side of 128 at that percentile
ALONG = 0.08         # --along: the smoothing's length, as a fraction of the crop's short side
WEBP_QUALITY = 88


def _smooth_along(a: np.ndarray, axis: int, length: int) -> np.ndarray:
    """A running mean `length` px long along one axis (edges held)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (length // 2, length - length // 2)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    c = np.insert(c, 0, 0, axis=axis)
    hi = np.take(c, range(length, c.shape[axis]), axis=axis)
    lo = np.take(c, range(0, c.shape[axis] - length), axis=axis)
    return ((hi - lo) / length).astype(np.float32)


def make(src: str, out: str, crop: tuple[int, int, int, int], size: int, median: int = 0,
         along: str | None = None) -> dict:
    im = ImageOps.exif_transpose(Image.open(src)).convert("L").crop(crop)
    if median > 1:
        im = im.filter(ImageFilter.MedianFilter(median | 1))
    im = im.filter(ImageFilter.GaussianBlur(DENOISE))
    radius = max(2.0, BLUR * min(im.size))
    a = np.asarray(im, dtype=np.float32)
    low = np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    detail = a - low
    if along:
        detail = _smooth_along(detail, 0 if along == "y" else 1, max(3, round(ALONG * min(im.size))))
    floor = CORE * float(np.median(np.abs(detail)))
    detail = np.sign(detail) * np.maximum(np.abs(detail) - floor, 0)
    peak = float(np.quantile(np.abs(detail), PEAK)) or 1.0
    grey = np.clip(128 + detail * (REACH / peak), 0, 255).astype(np.uint8)
    tex = Image.fromarray(grey, "L")
    long = max(tex.size)
    tex = tex.resize((round(tex.width * size / long), round(tex.height * size / long)), Image.LANCZOS)
    tex.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
    g = np.asarray(tex, dtype=np.float32)
    return {"w": tex.width, "h": tex.height,
            "spread": round(float(np.quantile(g, 0.99) - np.quantile(g, 0.01)), 1)}


def _overlay(base: Image.Image, tex: Image.Image, opacity: float) -> Image.Image:
    """CSS mix-blend-mode: overlay, at `opacity`, for the contact sheet."""
    b = np.asarray(base.convert("RGB"), dtype=np.float32) / 255
    t = np.asarray(tex.convert("L").resize(base.size), dtype=np.float32)[..., None] / 255
    o = np.where(b < 0.5, 2 * b * t, 1 - 2 * (1 - b) * (1 - t))
    mix = b + (o - b) * opacity
    return Image.fromarray(np.clip(mix * 255, 0, 255).astype(np.uint8), "RGB")


def sheet(out: str, photo: str, textures: list[str]) -> None:
    base = ImageOps.fit(Image.open(photo).convert("RGB"), (640, 360))
    tiles = [base] + [_overlay(base, Image.open(t), 0.6) for t in textures]
    canvas = Image.new("RGB", (640 * 2, 360 * ((len(tiles) + 1) // 2)), "black")
    for i, tile in enumerate(tiles):
        canvas.paste(tile, ((i % 2) * 640, (i // 2) * 360))
    canvas.save(out, quality=90)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("src")
    m.add_argument("out")
    m.add_argument("--crop", type=int, nargs=4, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    m.add_argument("--size", type=int, default=1024)
    m.add_argument("--median", type=int, default=0)
    m.add_argument("--along", choices=("x", "y"))
    s = sub.add_parser("sheet")
    s.add_argument("out")
    s.add_argument("photo")
    s.add_argument("textures", nargs="+")
    args = ap.parse_args()
    if args.cmd == "make":
        print(json.dumps(make(args.src, args.out, tuple(args.crop), args.size, args.median, args.along)))
    else:
        sheet(args.out, args.photo, args.textures)


if __name__ == "__main__":
    main()
