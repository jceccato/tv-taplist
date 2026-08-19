#!/usr/bin/env bash
# Print the CHANGELOG.md section for a release tag, for use as the GitHub
# Release body.
#
#   scripts/release_notes.sh v1.3.1
#
# Exits non-zero with a message on stderr when there is no section for the tag.
# The publish workflow calls this BEFORE building anything, so a tag without a
# changelog entry fails the release early rather than publishing an image and a
# release that says nothing.
set -euo pipefail

TAG="${1:-}"
CHANGELOG="${2:-$(dirname "$0")/../CHANGELOG.md}"

if [ -z "$TAG" ]; then
  echo "usage: $0 <tag> [changelog]" >&2
  exit 2
fi

# Match "## v1.3.1" exactly at the start of a heading, so v1.3.1 does not match
# a v1.3.10 heading. Everything up to the next "## " heading is the section;
# the trailing "---" separator is dropped.
SECTION="$(awk -v tag="$TAG" '
  $0 ~ "^## " tag "([ ]|$)" { found = 1; next }
  found && /^## / { exit }
  found { print }
' "$CHANGELOG")"

# Strip leading/trailing blank lines and a trailing horizontal rule.
SECTION="$(printf '%s\n' "$SECTION" | sed -e 's/[[:space:]]*$//' | awk '
  { lines[NR] = $0 }
  END {
    start = 1; end = NR
    while (start <= end && lines[start] == "") start++
    while (end >= start && (lines[end] == "" || lines[end] == "---")) end--
    for (i = start; i <= end; i++) print lines[i]
  }
')"

if [ -z "$SECTION" ]; then
  echo "No CHANGELOG.md section found for ${TAG}." >&2
  echo "Add a '## ${TAG} - YYYY-MM-DD' section describing what changed for the" >&2
  echo "operator, then re-tag. Every release needs one." >&2
  exit 1
fi

printf '%s\n' "$SECTION"
