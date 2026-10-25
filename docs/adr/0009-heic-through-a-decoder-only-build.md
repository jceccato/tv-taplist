# HEIC is read through a decoder-only libheif build

Status: accepted

iPhones save photos as HEIC by default, so the album (issue #47) must read it.
The ready-made `pillow-heif` wheels bundle x265, a GPLv2-only HEVC *encoder*,
which does not combine with this repository's AGPL-3.0. Reading a photo needs
only a decoder. So the image builds HEIC support from Debian's libheif with the
libde265 decoder plugin and no x265 plugin, at the cost of a slower Docker build
and a little Dockerfile upkeep.

Do not "simplify" this by adding `pillow-heif` from PyPI: that reintroduces
the licence conflict this decision exists to avoid. Camera RAW is a different
case: `rawpy` (MIT, LibRaw) is used, and the camera's embedded JPEG preview
is preferred over a full RAW decode for speed on a Raspberry Pi.
