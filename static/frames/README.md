# Realistic frame art (issue #61)

The "Realistic" frames of the framed print. Each file is a photograph-like
picture frame, seen straight-on, with its opening cut out to transparent. The
display draws one with CSS `border-image` (`FRAME_ART` in `static/js/display.js`
holds each file's side widths), so the corners keep their carving and the sides
stretch to any screen. Nothing is fetched from outside the box.

## Provenance

- **Generated** on 2026-10-26 with Google's Gemini image model Nano Banana 2.1
  (`gemini-nano-banana-2.1`) for this project: a first round at 1K (three
  concepts per kind of frame), the maintainer's picks re-rendered at 2K. They are
  machine-generated images, not photographs of anyone's work.
- **Cut out** with `scripts/frame_cutout.py cut <in>.jpg <out>.webp --inset 3 --width 1376`:
  the opening is generated as flat #FF00FF magenta, found as a rectangle, cut a
  few pixels wider than the key reaches and the edge despilled; every file
  passed the tool's left-over-magenta check. The tool prints the side widths
  that go into `FRAME_ART`.
- **Adding one**: generate with the prompt below, cut it, add the file here, its
  key to `REALISTIC_FRAMES` (`app/config_store.py`), `FRAMES` and `FRAME_ART`
  (`display.js`) and the Admin's Frame list. A guard test ties them together.

## Prompts

Round 1, every frame: "A photorealistic studio photograph of an empty rectangular picture frame, shot from dead straight-on with no perspective at all, the frame perfectly level and square to the camera. The frame MUST fill the entire image edge to edge, its outer edge touching all four edges of the image, NO margin, NO wall, NO background visible anywhere outside it. All four sides of the moulding are exactly the same width, about 9 percent of the image height, joined at clean 45-degree mitred corners. Soft, even gallery lighting from the top left, so the top and left sides catch a little more light. The opening inside the frame MUST be a perfectly flat, solid #FF00FF magenta rectangle with crisp straight edges: no texture, no gradient, no shadow, no glass, no reflection, no mat or mount, no picture. Do NOT use any magenta or pink in the frame itself. No text, no logos, no hanging hardware. The frame: " followed by the frame's own description:

| File | Description |
| ---- | ----------- |
| `real-oak-limed.webp` | oak with a stepped, two-tier gallery moulding, light limed finish showing open grain. |
| `real-oak-honey.webp` | oak with a wide flat face and a thin raised inner lip, mid-tone honey stain, visible ray flecks. |
| `real-walnut.webp` | walnut with a classic scooped cove moulding, deep brown with subtle highlights. |
| `real-gilded-baroque.webp` | ornate baroque gilded frame in antique gold leaf, carved acanthus scrolls along each side and larger carved corner ornaments, slightly worn gilding showing red bole in the recesses. |
| `real-gilded-classic.webp` | classic gold leaf frame with a carved bead-and-reel inner edge and a smooth swept outer profile, softly burnished. |
| `real-black-ash.webp` | black ash frame with a satin finish, open grain faintly visible through the black stain. |
| `real-white-stepped.webp` | white frame with a stepped double-tier moulding, gloss white with soft shadows in the steps. |
| `real-barnwood.webp` | rustic reclaimed barn-wood frame, weathered grey-brown boards with nail holes, saw marks and cracks, rough but straight. |
| `real-farmhouse.webp` | distressed farmhouse frame, chalky cream paint worn back to bare wood on the edges. |
| `real-dark-timber.webp` | rough-sawn dark timber frame with a deep brown stain and pronounced knots. |

Final (2K), with the round-1 pick as the attached reference: "Recreate the first attached picture frame faithfully as a crisp, high-detail final render. Keep its moulding, material, colour, carving and lighting exactly; keep it dead straight-on with no perspective, filling the whole image edge to edge with no wall or background outside it, all four sides the same width, clean 45-degree mitred corners. The opening MUST stay a perfectly flat, solid #FF00FF magenta rectangle with crisp straight edges: no texture, no gradient, no shadow, no glass. No magenta or pink in the frame itself. No text, no logos."
