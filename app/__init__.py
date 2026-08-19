"""TV Tap List - offline-first digital beer tap list appliance."""
import os

# ---------------------------------------------------------------------------
# The running version is a BUILD artefact, not something the source tree can
# know. The Docker build takes a VERSION build-arg (the git tag on a release,
# the branch name or short SHA otherwise) and bakes it in as TVTAPLIST_VERSION;
# outside a container there is no release to name, hence "dev".
#
# A hardcoded literal used to sit here and was two releases stale before anyone
# noticed (issue #25). A source file cannot keep that true, so do not put one
# back - name the env var, not a number.
# ---------------------------------------------------------------------------
VERSION_ENV = "TVTAPLIST_VERSION"
VERSION_FALLBACK = "dev"

# The conventional name, for anything that reaches for it (a log line, a
# diagnostic, a future --version flag). Snapshotted at import; use
# update_check.current_version() for a live read. The env cannot change under a
# running container, so the two only differ in tests that monkeypatch it.
__version__ = os.environ.get(VERSION_ENV, VERSION_FALLBACK)
