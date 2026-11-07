"""Installing mf6 (Linux server, Windows pc): fixed URL, SHA-256 checked, only the executable
extracted. The download itself is faked with a local zip."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from gwproducten.products.peilopzet_grid import mf6


@pytest.fixture(params=[("linux", "mf6"), ("win32", "mf6.exe")], ids=["linux", "windows"])
def fake_release(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    platform, member = request.param
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, b"#!/bin/sh\necho mf6\n")
        zf.writestr("other_tool", b"not extracted")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(mf6, "DOWNLOADS", {platform: (archive.as_uri(), digest, member)})
    monkeypatch.setattr(mf6, "_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(mf6, "_exe", lambda: None)
    monkeypatch.setattr(mf6, "_platform", lambda: platform)
    return tmp_path / "cache", member


def test_download_with_matching_checksum_installs_only_mf6(fake_release) -> None:
    cache, member = fake_release
    assert mf6.ensure_mf6() == str(cache / member)
    assert sorted(p.name for p in cache.iterdir()) == [member]


def test_download_with_wrong_checksum_is_refused(fake_release, monkeypatch) -> None:
    cache, member = fake_release
    ((platform, (url, _, name)),) = mf6.DOWNLOADS.items()
    monkeypatch.setattr(mf6, "DOWNLOADS", {platform: (url, "0" * 64, name)})
    assert mf6.ensure_mf6() is None
    assert not (cache / member).exists()


def test_unreachable_download_does_not_raise(fake_release, monkeypatch) -> None:
    cache, _ = fake_release
    ((platform, (_, sha, name)),) = mf6.DOWNLOADS.items()
    missing = (cache.parent / "missing.zip").as_uri()
    monkeypatch.setattr(mf6, "DOWNLOADS", {platform: (missing, sha, name)})
    assert mf6.ensure_mf6() is None


def test_pinned_release_is_mf6_6_7_0() -> None:
    """Both platforms point at the same fixed release (29.0 = MODFLOW 6.7.0)."""
    assert set(mf6.DOWNLOADS) == {"linux", "win32"}
    for url, sha, _ in mf6.DOWNLOADS.values():
        assert "/releases/download/29.0/" in url and len(sha) == 64
