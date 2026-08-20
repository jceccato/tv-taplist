"""Durability checks: the mount signal, the DDI truth table, and the banner.

The DDI cases are driven through `evaluate_identity` with explicit paths rather
than through the real container-local location, so the suite never touches
/var/lib/taplist and every row is reproducible on any host.
"""
from __future__ import annotations

import logging

import pytest

from app import persistence
from app.paths import PROJECT_ROOT


# ---- signal (a): is the data directory a mount? ------------------------

MOUNTINFO = (
    "23 28 0:22 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw\n"
    "29 22 0:26 / /data rw,relatime - ext4 /dev/sdb rw\n"
    "31 22 0:27 / /var/lib/taplist\\040odd rw,relatime - ext4 /dev/sdb rw\n"
)

MOUNTINFO_UNMAPPED = (
    "23 28 0:22 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw\n"
    "24 28 0:23 / / rw,relatime - overlay overlay rw\n"
)


def _mountinfo(tmp_path, text: str) -> str:
    p = tmp_path / "mountinfo"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_mount_points_decodes_octal_escapes(tmp_path):
    points = persistence.mount_points(_mountinfo(tmp_path, MOUNTINFO))
    assert "/data" in points
    assert "/var/lib/taplist odd" in points


def test_mount_points_tolerates_a_missing_mountinfo(tmp_path):
    assert persistence.mount_points(str(tmp_path / "nope")) == set()


def test_mapped_data_dir_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: True)
    assert persistence.is_data_dir_mapped("/data", _mountinfo(tmp_path, MOUNTINFO)) is True


def test_unmapped_data_dir_is_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: True)
    assert (
        persistence.is_data_dir_mapped("/data", _mountinfo(tmp_path, MOUNTINFO_UNMAPPED))
        is False
    )


def test_outside_a_container_the_mount_check_does_not_run(tmp_path, monkeypatch):
    """A developer's local folder is not a misconfiguration - stay silent."""
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: False)
    assert persistence.is_data_dir_mapped("/data", _mountinfo(tmp_path, MOUNTINFO_UNMAPPED)) is None


def test_unreadable_mountinfo_is_not_read_as_unmapped(tmp_path, monkeypatch):
    """No answer beats a wrong answer: an empty mountinfo means "cannot tell"."""
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: True)
    assert persistence.is_data_dir_mapped("/data", _mountinfo(tmp_path, "")) is None


# ---- the Dockerfile guard ----------------------------------------------

def test_dockerfile_declares_no_volume_for_the_data_dir():
    """Signal (a) is only a clean boolean while the image declares no VOLUME.

    Restoring `VOLUME ["/data"]` (an easy "Docker best practice" edit) would put
    an anonymous volume back under an unmapped data directory, which is
    indistinguishable from a named one at runtime - the check would go quiet and
    no other test would notice.
    """
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    directives = [
        line for line in text.splitlines()
        if line.strip().upper().startswith("VOLUME")
    ]
    assert directives == [], f"Dockerfile must declare no VOLUME: {directives}"


# ---- signal (b): the DDI truth table -----------------------------------
# Every row asserts the verdict *and* what happened to the data directory's
# identifier, because "warned once" and "preserved the authority" are separate
# promises and a regression could break either alone.

@pytest.fixture
def ddi(tmp_path):
    """(container copy path, data directory copy path), neither existing yet."""
    container = tmp_path / "container" / "data_dir_id"
    data = tmp_path / "data" / persistence.DDI_FILENAME
    return container, data


def _read(path):
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def test_row1_first_run_mints_one_identity_into_both_copies(ddi):
    container, data = ddi
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_FIRST_RUN, False)
    assert _read(data) == _read(container) == result.identity
    assert result.identity


def test_row2_container_recreate_adopts_and_preserves_the_data_identity(ddi):
    container, data = ddi
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("abc123\n", encoding="utf-8")
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_ADOPTED, False)
    assert _read(data) == "abc123", "the data directory is the authority: preserve it"
    assert _read(container) == "abc123"


def test_row3_a_wiped_data_dir_warns_and_rewrites_the_identifier(ddi):
    container, data = ddi
    container.parent.mkdir(parents=True, exist_ok=True)
    container.write_text("abc123\n", encoding="utf-8")
    data.parent.mkdir(parents=True, exist_ok=True)
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_WIPED, True)
    # Written back rather than freshly minted, so a data directory that returns
    # (a late-mounting NAS) does not produce a second, misleading warning.
    assert _read(data) == "abc123"


def test_row4_an_unchanged_pair_is_a_normal_boot(ddi):
    container, data = ddi
    for p in (container, data):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("abc123\n", encoding="utf-8")
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_UNCHANGED, False)
    assert _read(data) == "abc123", "a normal boot must not rewrite anything"
    assert _read(container) == "abc123"


def test_row5_a_different_populated_dir_warns_and_adopts_it(ddi):
    container, data = ddi
    container.parent.mkdir(parents=True, exist_ok=True)
    container.write_text("abc123\n", encoding="utf-8")
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("def456\n", encoding="utf-8")
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_REPLACED, True)
    assert _read(data) == "def456", "the data directory on disk stays the authority"
    assert _read(container) == "def456"


def test_a_detected_wipe_warns_exactly_once(ddi):
    """The boot after a wipe is an ordinary boot - the identifiers now agree."""
    container, data = ddi
    container.parent.mkdir(parents=True, exist_ok=True)
    container.write_text("abc123\n", encoding="utf-8")
    data.parent.mkdir(parents=True, exist_ok=True)
    assert persistence.evaluate_identity(container, data).warn is True
    second = persistence.evaluate_identity(container, data)
    assert (second.state, second.warn) == (persistence.DDI_UNCHANGED, False)


def test_an_unreadable_identity_file_is_inconclusive_not_a_wipe(ddi, monkeypatch):
    """A transient read failure must not warn, and must not clobber the identity.

    Docker Desktop bind mounts on Windows do misreport reads occasionally; the
    config store carries the same guard for the same reason.
    """
    container, data = ddi
    for p in (container, data):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("abc123\n", encoding="utf-8")

    real_read = persistence.Path.read_text

    def flaky(self, *a, **kw):
        if self == data:
            raise OSError("transient bind-mount read failure")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(persistence.Path, "read_text", flaky)
    result = persistence.evaluate_identity(container, data)
    assert (result.state, result.warn) == (persistence.DDI_INCONCLUSIVE, False)
    monkeypatch.undo()
    assert _read(data) == "abc123"


def test_outside_a_container_the_identity_check_does_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: False)
    result = persistence.check_identity(tmp_path, tmp_path / "container_id")
    assert result.state == persistence.DDI_SKIPPED
    assert not (tmp_path / persistence.DDI_FILENAME).exists()


# ---- the boot verdict ---------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_verdict():
    yield
    persistence._verdict = persistence.VERDICT_OK


def _boot(monkeypatch, tmp_path, *, mapped: bool):
    """Run the startup checks against a fake container."""
    monkeypatch.setattr(persistence, "_dockerenv_exists", lambda: True)
    monkeypatch.setattr(
        persistence,
        "is_data_dir_mapped",
        lambda data_dir=None, mountinfo_path=None: mapped,
    )
    return persistence.run_startup_checks(tmp_path / "data", tmp_path / "cl" / "id")


def test_a_mapped_first_boot_is_silent(monkeypatch, tmp_path, caplog):
    (tmp_path / "data").mkdir()
    with caplog.at_level(logging.WARNING, logger="taplist.persistence"):
        verdict = _boot(monkeypatch, tmp_path, mapped=True)
    assert verdict == persistence.VERDICT_OK
    assert persistence.admin_banner() is None
    assert caplog.records == []


def test_an_unmapped_boot_warns_and_sets_the_banner(monkeypatch, tmp_path, caplog):
    (tmp_path / "data").mkdir()
    with caplog.at_level(logging.WARNING, logger="taplist.persistence"):
        verdict = _boot(monkeypatch, tmp_path, mapped=False)
    assert verdict == persistence.VERDICT_NOT_MAPPED
    assert persistence.admin_banner() == persistence.VERDICT_NOT_MAPPED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_wiped_data_dir_sets_the_replaced_banner(monkeypatch, tmp_path, caplog):
    (tmp_path / "data").mkdir()
    (tmp_path / "cl").mkdir()
    (tmp_path / "cl" / "id").write_text("abc123\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="taplist.persistence"):
        verdict = _boot(monkeypatch, tmp_path, mapped=True)
    assert verdict == persistence.VERDICT_DATA_REPLACED
    assert persistence.admin_banner() == persistence.VERDICT_DATA_REPLACED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_signal_a_wins_when_both_signals_trip(monkeypatch, tmp_path):
    """The fixable warning must not have to compete with the unfixable one."""
    (tmp_path / "data").mkdir()
    (tmp_path / "cl").mkdir()
    (tmp_path / "cl" / "id").write_text("abc123\n", encoding="utf-8")
    verdict = _boot(monkeypatch, tmp_path, mapped=False)
    assert verdict == persistence.VERDICT_NOT_MAPPED
    # ... and the DDI bookkeeping still ran, so the next boot is not a surprise.
    assert _read(tmp_path / "data" / persistence.DDI_FILENAME) == "abc123"


def test_demo_mode_suppresses_the_banner_and_logs_at_info(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("DEMO_MODE", "true")
    (tmp_path / "data").mkdir()
    with caplog.at_level(logging.INFO, logger="taplist.persistence"):
        verdict = _boot(monkeypatch, tmp_path, mapped=False)
    assert verdict == persistence.VERDICT_NOT_MAPPED
    assert persistence.admin_banner() is None, "demo boxes are disposable and say so"
    assert [r.levelno for r in caplog.records if "not a mapped host directory" in r.getMessage()] == [
        logging.INFO
    ]


def test_startup_checks_never_raise(monkeypatch, tmp_path):
    """A durability warning must never become an outage."""
    def boom(*a, **kw):
        raise RuntimeError("mountinfo exploded")

    monkeypatch.setattr(persistence, "is_data_dir_mapped", boom)
    assert persistence.run_startup_checks(tmp_path, tmp_path / "id") == persistence.VERDICT_OK
