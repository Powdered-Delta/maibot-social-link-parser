# -*- coding: utf-8 -*-
"""ffmpeg 自举模块与 ffmpeg 命令路径注入测试。"""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from maibot_social_link_parser.core import download, ffmpeg, utils


@pytest.fixture(autouse=True)
def reset_ffmpeg_state():
    """隔离模块级 configured ffmpeg 状态。"""
    ffmpeg.set_configured_ffmpeg(None)
    yield
    ffmpeg.set_configured_ffmpeg(None)


def make_tar_gz(archive: Path, member_name: str, content: bytes) -> None:
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo(member_name)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))


def make_zip(archive: Path, member_name: str, content: bytes) -> None:
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member_name, content)


class TestPlatform:
    def test_default_urls_per_platform(self) -> None:
        assert ffmpeg.default_download_url("linux64").endswith("linux64-gpl.tar.xz")
        assert ffmpeg.default_download_url("linuxarm64").endswith("linuxarm64-gpl.tar.xz")
        assert ffmpeg.default_download_url("win64").endswith("win64-gpl.zip")
        assert ffmpeg.default_download_url("winarm64").endswith("winarm64-gpl.zip")

    def test_unknown_platform_has_no_url(self) -> None:
        assert ffmpeg.default_download_url("bogus") is None

    def test_binary_name_matches_os(self) -> None:
        expected = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        assert ffmpeg.ffmpeg_binary_name() == expected


class TestLocalBinary:
    def test_local_binary_found(self, tmp_path: Path) -> None:
        binary = tmp_path / "ffmpeg" / ffmpeg.ffmpeg_binary_name()
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"elf")
        assert ffmpeg.local_binary(tmp_path) == binary

    def test_local_binary_missing(self, tmp_path: Path) -> None:
        assert ffmpeg.local_binary(tmp_path) is None

    def test_resolve_prefers_local_over_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        local = tmp_path / "ffmpeg" / ffmpeg.ffmpeg_binary_name()
        local.parent.mkdir(parents=True)
        local.write_bytes(b"elf")
        monkeypatch.setattr(ffmpeg.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        assert ffmpeg.resolve_ffmpeg_path(tmp_path) == local

    def test_resolve_falls_back_to_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ffmpeg.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        assert ffmpeg.resolve_ffmpeg_path(tmp_path) == Path("/usr/bin/ffmpeg")


class TestExtract:
    def test_extract_binary_from_tar_gz(self, tmp_path: Path) -> None:
        archive = tmp_path / "ffmpeg.tar.gz"
        make_tar_gz(archive, "ffmpeg-build/bin/ffmpeg", b"tar-binary")
        target = tmp_path / "out"
        result = ffmpeg._extract_binary(archive, target, "ffmpeg")
        assert result == target / "ffmpeg"
        assert (target / "ffmpeg").read_bytes() == b"tar-binary"

    def test_extract_binary_from_zip(self, tmp_path: Path) -> None:
        archive = tmp_path / "ffmpeg.zip"
        make_zip(archive, "ffmpeg-build/bin/ffmpeg.exe", b"zip-binary")
        target = tmp_path / "out"
        result = ffmpeg._extract_binary(archive, target, "ffmpeg.exe")
        assert result == target / "ffmpeg.exe"
        assert (target / "ffmpeg.exe").read_bytes() == b"zip-binary"

    def test_extract_ignores_non_bin_members(self, tmp_path: Path) -> None:
        archive = tmp_path / "ffmpeg.tar.gz"
        make_tar_gz(archive, "ffmpeg-build/doc/readme.txt", b"not a binary")
        assert ffmpeg._extract_binary(archive, tmp_path / "out", "ffmpeg") is None


class TestEnsure:
    def test_reuses_existing_local_binary(self, tmp_path: Path) -> None:
        binary = tmp_path / "ffmpeg" / ffmpeg.ffmpeg_binary_name()
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"elf")
        result = asyncio.run(ffmpeg.ensure_ffmpeg(tmp_path, enabled=True))
        assert result == binary
        assert ffmpeg.configured_ffmpeg() == binary

    def test_disabled_returns_existing_or_none(self, tmp_path: Path) -> None:
        assert asyncio.run(ffmpeg.ensure_ffmpeg(tmp_path, enabled=False)) is None

    def test_download_and_extract_via_file_url(self, tmp_path: Path) -> None:
        binary_name = ffmpeg.ffmpeg_binary_name()
        archive = tmp_path / "ffmpeg.tar.gz"
        make_tar_gz(archive, f"ffmpeg-build/bin/{binary_name}", b"bootstrapped")
        data_dir = tmp_path / "data"
        result = asyncio.run(
            ffmpeg.ensure_ffmpeg(data_dir, enabled=True, download_url=archive.as_uri())
        )
        expected = data_dir / "ffmpeg" / binary_name
        assert result == expected
        assert expected.read_bytes() == b"bootstrapped"
        assert ffmpeg.configured_ffmpeg() == expected

    def test_unsupported_platform_or_url_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ffmpeg, "detect_platform", lambda: None)
        assert asyncio.run(ffmpeg.ensure_ffmpeg(tmp_path, enabled=True)) is None


class TestCommandInjection:
    async def _run_exec(self, cmd: list[str]) -> list[str]:
        captured: list[str] = []

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured.extend(args)
            return FakeProcess()

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fake_create_subprocess_exec  # type: ignore[assignment]
        try:
            await utils.exec_ffmpeg_cmd(cmd)
        finally:
            asyncio.create_subprocess_exec = original  # type: ignore[assignment]
        return captured

    def test_substitutes_configured_path(self, tmp_path: Path) -> None:
        fake = tmp_path / "bin" / "ffmpeg"
        fake.parent.mkdir(parents=True)
        fake.write_bytes(b"x")
        ffmpeg.set_configured_ffmpeg(fake)
        captured = asyncio.run(self._run_exec(["ffmpeg", "-y", "-i", "a.mp4"]))
        assert captured[0] == str(fake)
        assert captured[1:] == ["-y", "-i", "a.mp4"]

    def test_keeps_default_when_unconfigured(self) -> None:
        captured = asyncio.run(self._run_exec(["ffmpeg", "-version"]))
        assert captured[0] == "ffmpeg"


class TestYtdlpLocation:
    def test_ffmpeg_location_uses_configured_parent(self, tmp_path: Path) -> None:
        binary = tmp_path / "ffmpeg" / ffmpeg.ffmpeg_binary_name()
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"x")
        ffmpeg.set_configured_ffmpeg(binary)
        assert download._ffmpeg_location() == str(binary.parent)

    def test_ffmpeg_location_none_when_unconfigured(self) -> None:
        assert download._ffmpeg_location() is None
