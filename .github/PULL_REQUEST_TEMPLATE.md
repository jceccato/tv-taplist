## Description

<!-- Briefly describe what this PR does and why. -->

## Related issue

<!-- Link to the issue this PR addresses. -->
Fixes #

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactor (no functional change)
- [ ] Other (describe below)

## Checklist

- [ ] **Tests pass:** `python -m pytest -q` - all 243 tests passing
  (CI runs the same suite on Python 3.12 and blocks the publish if it is red)
- [ ] **New tests added** for any new functionality or bug fix
- [ ] **Code style** matches the surrounding code (defensive type coercion,
  comments explain *why*, docstrings present)
- [ ] **Docker test passes** (if Dockerfile/entrypoint/startup changed):
  `bash scripts/docker_test.sh`
- [ ] **No secrets in diff:** `.env`, `taplist_data/`, Brewfather keys are not
  committed (verify with `git status`)
- [ ] **Docs updated** if the change affects user-facing behavior
- [ ] **`MAPPING_VERSION` bumped** in `app/brewfather.py` if Brewfather
  extraction logic changed
- [ ] **`CHANGELOG.md` updated** if this is user-facing. Add it under the
  `## Unreleased` heading at the top, creating that section if it is not there.
  Write it when the change merges, not on release day - a release tag cannot
  publish without its section. See `docs/VERSIONING.md`.

## Screenshots (if UI change)

<!-- Drag and drop screenshots here. -->

## Additional notes

<!-- Anything else reviewers should know. -->
