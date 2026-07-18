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

- [ ] **Tests pass:** `python -m pytest -q` — all 127+ tests passing
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

## Screenshots (if UI change)

<!-- Drag and drop screenshots here. -->

## Additional notes

<!-- Anything else reviewers should know. -->
