# Crease textures (issue #63)

The "Creases" overlay of the framed print. Each file is a fold or crumple from
a photograph of real paper, reduced to its fine detail on neutral grey (128).
The display lays one over the print and its mount with `mix-blend-mode:
overlay`, under which the grey changes nothing, so only the fold shows. It is
turned, flipped, scaled and shifted at random for each photo (`CREASES` and
`layoutCrease` in `static/js/display.js`). Nothing is fetched from outside the box.

## Provenance

Every source is **CC0 1.0** (public domain dedication): no attribution is
required, and it is given here anyway so the files can be traced.

| File | Source | Author | Licence |
| ---- | ------ | ------ | ------- |
| `crease-fold.webp` | Wikimedia Commons, "Paper-folding-5.jpg", https://commons.wikimedia.org/wiki/File:Paper-folding-5.jpg | Miraifusionzt4 | CC0 1.0, https://creativecommons.org/publicdomain/zero/1.0/ |
| `crease-cross.webp` | Wikimedia Commons, "Paper-folding-1b.jpg", https://commons.wikimedia.org/wiki/File:Paper-folding-1b.jpg | Miraifusionzt4 | CC0 1.0, https://creativecommons.org/publicdomain/zero/1.0/ |
| `crease-crumpled.webp` | Wikimedia Commons, "Crumpled Paper.png", https://commons.wikimedia.org/wiki/File:Crumpled_Paper.png | Epicpanda65 | CC0 1.0, https://creativecommons.org/publicdomain/zero/1.0/ |

Each licence was read from the file's Commons page (its `LicenseShortName`
is CC0) on 2026-10-26, when the originals were downloaded.

## How they were made

With `scripts/crease_textures.py` from the full-size originals (coordinates are
in the original's pixels):

```bash
python scripts/crease_textures.py make Paper-folding-5.jpg static/creases/crease-fold.webp --crop 300 520 1140 1400 --along y
python scripts/crease_textures.py make Paper-folding-1b.jpg static/creases/crease-cross.webp --crop 520 560 1040 1240
python scripts/crease_textures.py make "Crumpled Paper.png" static/creases/crease-crumpled.webp --crop 40 40 3360 2400
```

- **Paper-folding-5**: one straight fold down the middle of the sheet, cropped
  clear of the cut notches. `--along y` smooths the detail along the fold, so the
  specks of dust on the photographed sheet average away and the line stays.
- **Paper-folding-1b**: the unfolded sheet's centre, where a vertical and a
  horizontal fold cross, cropped clear of its cut-out holes.
- **Crumpled Paper**: the whole sheet less a thin border.

A guard test checks that every file named in `CREASES` is here with a CC0 row in
the table above, and that each texture is neutral grey where there is no fold.

**Adding one**: find a CC0 photograph of folded or crumpled paper, record it in
the table, make it with the script, check it with
`python scripts/crease_textures.py sheet out.jpg photo.jpg static/creases/*.webp`,
and add its key to `CREASES`.

Considered and left out: ambientCG's "Paper 003" (CC0, crumpled), which turned
blotchy, like marble, once reduced to its detail.
