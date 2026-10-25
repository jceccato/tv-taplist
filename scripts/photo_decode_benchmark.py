"""Time how long the photo album takes to make Display copies, on the box it runs on.

Run inside the app's container, so it measures the image's own decoders (Pillow,
the decoder-only libheif and rawpy) on the real CPU. Each file goes through the
same pipeline the one-at-a-time queue uses (decode, upright, sRGB, fit, encode,
thumbnail), several times, and the script prints the best and median time per
file and which decoder path a RAW took (embedded preview or half-size develop).
Nothing under the data directory is read or written.

Usage, with the photos mounted into a throwaway container of the image:

    docker run --rm --entrypoint python -w /app \\
      -v "$PWD/scripts:/bench:ro" -v "$HOME/bench-photos:/photos:ro" \\
      tv-taplist:local /bench/photo_decode_benchmark.py /photos/*

Options: --size 4k|1080p (default 4k), --format webp|jpeg (default webp),
--runs N (default 3).
"""
from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.getcwd())          # the image's WORKDIR is /app, which holds `app`

from app import photo_library as lib  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--size", choices=lib.SIZES, default=lib.SIZE_4K)
    ap.add_argument("--format", choices=lib.FORMATS, default=lib.FORMAT_WEBP)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    pipe = lib.Pipeline(args.format, lib.TARGET_SIZE_1080P if args.size == lib.SIZE_1080P
                        else lib.TARGET_SIZE)
    model = Path("/proc/device-tree/model")
    print(f"machine: {model.read_text().strip(chr(0)) if model.exists() else platform.machine()}")
    print(f"cpus: {os.cpu_count()}  python {platform.python_version()}  "
          f"heic: {'yes' if lib.pillow_heif else 'NO'}  raw: {'yes' if lib.rawpy else 'NO'}")
    print(f"pipeline: {pipe.key}  runs per file: {args.runs}\n")

    failed = False
    for path in args.files:
        times, out = [], None
        try:
            for _ in range(max(1, args.runs)):
                started = time.perf_counter()
                out = lib._render(path, pipe)
                times.append(time.perf_counter() - started)
        except lib.UnreadablePhoto as exc:
            print(f"{path.name}: UNREADABLE ({exc})")
            failed = True
            continue
        mb = path.stat().st_size / 1024 ** 2
        print(f"{path.name}: {mb:.1f} MB, long side {out.long_side} px, via {out.source}, "
              f"copy {out.width}x{out.height} ({len(out.display) / 1024 ** 2:.1f} MB)")
        print(f"  best {min(times):.2f} s  median {statistics.median(times):.2f} s  "
              f"runs {', '.join(f'{t:.2f}' for t in times)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
