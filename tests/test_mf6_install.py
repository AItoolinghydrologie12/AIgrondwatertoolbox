"""Installing mf6 on the Linux server: fixed URL, SHA-256 checked, only mf6 extracted."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from gwproducten.products.peilopzet_grid import mf6


@pytest.fixture
def fake_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    archive = tmp_path / "linux.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mf6", b"#!/bin/sh\necho mf6\n")
        zf.writestr("other_tool", b"not extracted")
    monkeypatch.setattr(mf6, "LINUX_ZIP_URL", archive.as_uri())
    monkeypatch.setattr(mf6, "LINUX_ZIP_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(mf6, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(mf6, "_exe", lambda: None)
    monkeypatch.setattr(mf6.sys, "platform", "linux")
    return tmp_path / "cache"


def test_download_with_matching_checksum_installs_only_mf6(fake_release: Path) -> None:
    path = mf6.ensure_mf6()
    assert path == str(fake_release / "mf6")
    assert sorted(p.name for p in fake_release.iterdir()) == ["mf6"]


def test_download_with_wrong_checksum_is_refused(
    fake_release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mf6, "LINUX_ZIP_SHA256", "0" * 64)
    assert mf6.ensure_mf6() is None
    assert not (fake_release / "mf6").exists()


def test_unreachable_download_does_not_raise(
    fake_release: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mf6, "LINUX_ZIP_URL", (fake_release.parent / "missing.zip").as_uri())
    assert mf6.ensure_mf6() is None
