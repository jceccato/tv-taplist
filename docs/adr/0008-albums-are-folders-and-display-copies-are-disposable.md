# Albums are folders of untouched originals; Display copies are disposable

Status: accepted

The photo album (issue #47) stores each **Album** as a folder under
`/data/photos/`, holding the operator's original files unchanged, and loose
files at the top level form the album "Unsorted". Screens are only ever sent a
**Display copy**: upright, converted to sRGB, scaled up or down to the chosen
target (4K or 1080p), encoded as WebP (or JPEG in compatibility mode), and
**stripped of all metadata**. Display copies and Admin thumbnails live in a
cache the box rebuilds from the originals, so deleting it costs only
reprocessing time, which is the same lifecycle test as ADR-0002 and ADR-0006.

This extends ADR-0001: photos are plain files in the mapped data directory,
which the operator can add, move and delete by hand. Snapshots carry the album
settings but never the photos, because a photo folder can run to gigabytes and
the operator already owns it as files.

## Why not serve the originals

- Phone and camera files are often HEIC or RAW, which TV browsers cannot
  show, and are up to 250 MB.
- Leaving scaling to the browser gives a blurry upscale and stutters on weak
  TVs.
- `/api/board` and its images are public on the LAN, and originals carry GPS
  location. A screen never receives an original. Originals and thumbnails are
  served only behind the Admin login.

## Consequences

- Removing a photo moves it to `/data/photos_removed/` (**Removed**) with its
  album remembered, and it is **Purged** after a set number of days.
- WebP quality is 99. Measured over 24 photos fitted to 4K, that was the
  quality nearest 40 % of the lossless size for most of them (0.6-3.8 MB per
  copy). Changing the format or size setting rebuilds every copy in the
  background.

## Note, 2026-10-25: other ways in besides the folder

The operator no longer only changes albums by hand on disk. The Admin's Photos tab
uploads photos (issue #57) and creates, renames and deletes albums, moves photos
between albums, removes and restores them, and sets the Removed retention
(issue #58). The decision stands: the folders remain the source of truth and
hand-made changes are still picked up within a minute. Two consequences of the
Admin being a second writer:

- An Admin rename updates the ticked album names in the Default profile and every
  profile; a rename by hand cannot, so it appears as a new, unticked album.
- Removing, restoring, moving and renaming go through the photo library, so they
  take effect at once instead of waiting for the scan. A removed photo is a
  `<token>.<ext>` original beside a `<token>.json` record (album, name, removal
  time) in `photos_removed/`; its thumbnail stays in the cache so the Removed
  view can show it, but its Display copy is deleted at once.
