"""Tests for the ffmpeg installer: where it lives, what it downloads, how it unpacks.

Nothing here touches the network or the real per-user folder. Downloads are
served by a fake ``fetch`` from memory, archives are built in the tests, and
the check that the new copy runs is replaced by a fake ``verify`` (except in
the one test that exercises the default check on a file that is not a program).
Set ``JZR_TEST_NETWORK=1`` to also run the one test class that asks the real
sources what they publish.
"""

from __future__ import annotations

from collections.abc import Mapping
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from jzr_tools.core import ffmpeg_install
from jzr_tools.core.ffmpeg_install import (
    SOURCES,
    Archive,
    InstallError,
    Release,
    data_dir,
    install,
    install_dir,
    installed,
    manual_route,
    pick_source,
    remove,
    resolve,
    tool_path,
    up_to_date,
)

BTBN_HOST = "github.com/BtbN/FFmpeg-Builds"
MAC_HOST = "ffmpeg.martin-riedl.de"
CHECKSUMS_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/checksums.sha256"
DOWNLOAD_FOLDER = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"


def all_sources() -> list:
    return list(SOURCES.values() if isinstance(SOURCES, Mapping) else SOURCES)


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# --- where things live -------------------------------------------------------


class DataDirTests(unittest.TestCase):
    def test_jzr_data_dir_wins_on_every_platform(self) -> None:
        environ = {
            "JZR_DATA_DIR": str(Path("elsewhere") / "jzr-data"),
            "LOCALAPPDATA": r"C:\Users\x\AppData\Local",
            "XDG_DATA_HOME": str(Path(tempfile.gettempdir()) / "xdg"),
            "HOME": "home",
        }
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform):
                self.assertEqual(data_dir(environ, platform), Path(environ["JZR_DATA_DIR"]))

    def test_windows_uses_local_app_data(self) -> None:
        local = r"C:\Users\x\AppData\Local"
        self.assertEqual(
            data_dir({"LOCALAPPDATA": local, "HOME": "ignored"}, "win32"), Path(local) / "jzr"
        )

    def test_windows_without_local_app_data_uses_the_home_folder(self) -> None:
        self.assertEqual(
            data_dir({"HOME": "h"}, "win32"), Path("h") / "AppData" / "Local" / "jzr"
        )

    def test_windows_without_anything_still_ends_in_the_usual_place(self) -> None:
        self.assertEqual(data_dir({}, "win32").parts[-3:], ("AppData", "Local", "jzr"))

    def test_macos_uses_application_support(self) -> None:
        self.assertEqual(
            data_dir({"HOME": "h", "XDG_DATA_HOME": "ignored"}, "darwin"),
            Path("h") / "Library" / "Application Support" / "jzr",
        )

    def test_macos_without_home_still_ends_in_the_usual_place(self) -> None:
        self.assertEqual(
            data_dir({}, "darwin").parts[-3:], ("Library", "Application Support", "jzr")
        )

    def test_linux_uses_an_absolute_xdg_data_home(self) -> None:
        xdg = Path(tempfile.gettempdir()) / "xdg-share"
        self.assertEqual(
            data_dir({"XDG_DATA_HOME": str(xdg), "HOME": "h"}, "linux"), xdg / "jzr"
        )

    def test_linux_ignores_a_relative_or_empty_xdg_data_home(self) -> None:
        for value in ("relative/share", ""):
            with self.subTest(value=value):
                self.assertEqual(
                    data_dir({"XDG_DATA_HOME": value, "HOME": "h"}, "linux"),
                    Path("h") / ".local" / "share" / "jzr",
                )

    def test_linux_without_xdg_data_home_uses_home(self) -> None:
        self.assertEqual(
            data_dir({"HOME": "h"}, "linux"), Path("h") / ".local" / "share" / "jzr"
        )

    def test_other_unix_systems_follow_the_linux_rule(self) -> None:
        self.assertEqual(
            data_dir({"HOME": "h"}, "freebsd14"), Path("h") / ".local" / "share" / "jzr"
        )

    def test_linux_without_home_still_ends_in_the_usual_place(self) -> None:
        self.assertEqual(data_dir({}, "linux").parts[-3:], (".local", "share", "jzr"))

    def test_defaults_come_from_the_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"JZR_DATA_DIR": folder}):
                self.assertEqual(data_dir(), Path(folder))
                self.assertEqual(install_dir(), Path(folder) / "ffmpeg")
                self.assertEqual(
                    tool_path("ffmpeg").parent, Path(folder) / "ffmpeg" / "bin"
                )

    def test_install_dir_is_ffmpeg_inside_the_data_dir(self) -> None:
        environ = {"JZR_DATA_DIR": "data"}
        for platform in ("win32", "darwin", "linux"):
            with self.subTest(platform=platform):
                self.assertEqual(install_dir(environ, platform), Path("data") / "ffmpeg")

    def test_tool_path_has_exe_only_on_windows(self) -> None:
        environ = {"JZR_DATA_DIR": "data"}
        bin_dir = Path("data") / "ffmpeg" / "bin"
        for name in ("ffmpeg", "ffprobe"):
            self.assertEqual(tool_path(name, environ, "win32"), bin_dir / f"{name}.exe")
            self.assertEqual(tool_path(name, environ, "linux"), bin_dir / name)
            self.assertEqual(tool_path(name, environ, "darwin"), bin_dir / name)

    def test_default_platform_is_this_one(self) -> None:
        expected = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        self.assertEqual(tool_path("ffmpeg", {"JZR_DATA_DIR": "data"}).name, expected)


# --- which build for which machine --------------------------------------------


class PickSourceTests(unittest.TestCase):
    def test_the_table_has_the_six_supported_machines(self) -> None:
        rows = {(s.system, s.machine, s.host, s.key) for s in all_sources()}
        self.assertEqual(
            rows,
            {
                ("windows", "x64", BTBN_HOST, "win64"),
                ("windows", "arm64", BTBN_HOST, "winarm64"),
                ("linux", "x64", BTBN_HOST, "linux64"),
                ("linux", "arm64", BTBN_HOST, "linuxarm64"),
                ("macos", "arm64", MAC_HOST, "arm64"),
                ("macos", "x64", MAC_HOST, "amd64"),
            },
        )

    def test_every_spelling_of_a_machine_finds_its_source(self) -> None:
        cases = [
            (("Windows", "AMD64"), ("windows", "x64")),
            (("Windows", "x86_64"), ("windows", "x64")),
            (("windows", "x64"), ("windows", "x64")),
            (("Windows", "ARM64"), ("windows", "arm64")),
            (("Windows", "aarch64"), ("windows", "arm64")),
            (("Linux", "x86_64"), ("linux", "x64")),
            (("Linux", "AMD64"), ("linux", "x64")),
            (("Linux", "aarch64"), ("linux", "arm64")),
            (("Linux", "arm64"), ("linux", "arm64")),
            (("linux", "AARCH64"), ("linux", "arm64")),
            (("Darwin", "arm64"), ("macos", "arm64")),
            (("Darwin", "aarch64"), ("macos", "arm64")),
            (("Darwin", "x86_64"), ("macos", "x64")),
            (("DARWIN", "AMD64"), ("macos", "x64")),
        ]
        for (system, machine), (want_system, want_machine) in cases:
            with self.subTest(system=system, machine=machine):
                source = pick_source(system, machine)
                self.assertIsNotNone(source)
                self.assertEqual((source.system, source.machine), (want_system, want_machine))
                self.assertIn(source, all_sources())

    def test_machines_without_a_build_get_none(self) -> None:
        for system, machine in [
            ("Linux", "i686"),
            ("Linux", "armv7l"),
            ("FreeBSD", "amd64"),
            ("Windows", "x86"),
            ("Darwin", "i386"),
            ("Darwin", "ppc"),
            ("", ""),
        ]:
            with self.subTest(system=system, machine=machine):
                self.assertIsNone(pick_source(system, machine))

    def test_manual_route_per_system(self) -> None:
        self.assertEqual(manual_route("Windows"), "winget install Gyan.FFmpeg")
        self.assertEqual(manual_route("Darwin"), "brew install ffmpeg")
        self.assertEqual(manual_route("Linux"), "sudo apt install ffmpeg")
        self.assertEqual(manual_route("FreeBSD"), "sudo apt install ffmpeg")


# --- fake downloads ------------------------------------------------------------

class FakeResponse:
    def __init__(self, url: str, data: bytes, size: int | None) -> None:
        self.url = url
        self.size = size
        self._data = io.BytesIO(data)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, n: int = -1) -> bytes:
        return self._data.read(n)


class FakeFetch:
    """A ``fetch`` that answers from a dict of url -> (body, final url, size).

    ``final`` is None for no redirect; ``size`` may be None (unknown).
    A url that is not in the dict fails like a network error.
    """

    def __init__(self, pages: dict[str, tuple]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> FakeResponse:
        self.calls.append(url)
        if url not in self.pages:
            raise OSError(f"no route to {url}")
        page = self.pages[url]
        body, final, size = page
        return FakeResponse(final or url, body, size)


def refuse(url: str) -> FakeResponse:
    raise OSError("connection refused")


def checksum_lines(names: list[str]) -> bytes:
    return "".join(f"{sha(name)}  {name}\n" for name in names).encode()


BTBN_NAMES = [
    "ffmpeg-master-latest-win64-gpl-shared.zip",
    "ffmpeg-master-latest-win64-gpl.zip",
    "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
    "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip",
    "ffmpeg-n9.1-latest-win64-gpl-shared-9.1.zip",
    "ffmpeg-n10.0-latest-win64-gpl-shared-10.0.zip",
    "ffmpeg-n10.0-latest-win64-gpl-10.0.zip",
    "ffmpeg-n10.0-latest-win64-lgpl-shared-10.0.zip",
    "ffmpeg-n10.0-latest-win64-lgpl-10.0.zip",
    "ffmpeg-n12.0-latest-win64-gpl-shared-11.5.zip",
    "ffmpeg-n11.0-latest-winarm64-gpl-shared-11.0.zip",
    "ffmpeg-n11.0-latest-linux64-gpl-shared-11.0.tar.xz",
    "ffmpeg-n11.0-latest-linuxarm64-gpl-shared-11.0.tar.xz",
    "ffmpeg-n10.0-latest-linux64-gpl-shared-10.0.tar.xz",
    "ffmpeg-n9.0-latest-linuxarm64-gpl-shared-9.0.tar.xz",
    "ffmpeg-n9.1-latest-linux64-gpl-shared-9.1.tar.xz",
]


def btbn_fetch(names: list[str], size: int | None = 85_871_624, **extra: tuple) -> FakeFetch:
    pages: dict[str, tuple] = {CHECKSUMS_URL: (checksum_lines(names), None, None)}
    for name in names:
        pages[DOWNLOAD_FOLDER + name] = (b"", None, size)
    pages.update(extra)
    return FakeFetch(pages)


class ResolveBtbnTests(unittest.TestCase):
    def test_picks_the_highest_release_gpl_shared_build_for_the_machine(self) -> None:
        cases = [
            (("Windows", "AMD64"), "10.0", "ffmpeg-n10.0-latest-win64-gpl-shared-10.0.zip"),
            (("Windows", "ARM64"), "11.0", "ffmpeg-n11.0-latest-winarm64-gpl-shared-11.0.zip"),
            (("Linux", "x86_64"), "11.0", "ffmpeg-n11.0-latest-linux64-gpl-shared-11.0.tar.xz"),
            (("Linux", "aarch64"), "11.0", "ffmpeg-n11.0-latest-linuxarm64-gpl-shared-11.0.tar.xz"),
        ]
        for (system, machine), version, name in cases:
            with self.subTest(system=system, machine=machine):
                source = pick_source(system, machine)
                release = resolve(source, fetch=btbn_fetch(BTBN_NAMES))
                self.assertEqual(release.source, source)
                self.assertEqual(release.version, version)
                (archive,) = release.archives
                self.assertEqual(archive.name, name)
                self.assertEqual(archive.url, DOWNLOAD_FOLDER + name)
                self.assertEqual(archive.sha256, sha(name))
                self.assertEqual(archive.size, 85_871_624)
                self.assertEqual(release.size, 85_871_624)

    def test_versions_compare_as_numbers_whatever_the_order_in_the_list(self) -> None:
        names = [
            "ffmpeg-n9.1-latest-win64-gpl-shared-9.1.zip",
            "ffmpeg-n10.0-latest-win64-gpl-shared-10.0.zip",
            "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip",
        ]
        for ordered in (names, names[::-1]):
            with self.subTest(order=ordered[0]):
                release = resolve(pick_source("Windows", "AMD64"), fetch=btbn_fetch(ordered))
                self.assertEqual(release.version, "10.0")

    def test_nine_one_beats_nine_zero_and_eight_one(self) -> None:
        names = [n for n in BTBN_NAMES if "n10.0" not in n and "n12.0" not in n]
        release = resolve(pick_source("Windows", "AMD64"), fetch=btbn_fetch(names))
        self.assertEqual(release.version, "9.1")

    def test_a_list_with_only_master_or_other_flavours_is_a_layout_change(self) -> None:
        names = [
            "ffmpeg-master-latest-win64-gpl-shared.zip",
            "ffmpeg-n10.0-latest-win64-gpl-10.0.zip",
            "ffmpeg-n10.0-latest-win64-lgpl-shared-10.0.zip",
            "ffmpeg-master-latest-linux64-gpl-shared.tar.xz",
            "ffmpeg-n10.0-latest-linux64-gpl-10.0.tar.xz",
            "ffmpeg-n10.0-latest-winarm64-gpl-shared-10.0.zip",
            "ffmpeg-n10.0-latest-linuxarm64-gpl-shared-10.0.tar.xz",
        ]
        for system, machine, route in [
            ("Windows", "AMD64", "winget install Gyan.FFmpeg"),
            ("Linux", "x86_64", "sudo apt install ffmpeg"),
        ]:
            with self.subTest(system=system):
                with self.assertRaises(InstallError) as caught:
                    resolve(pick_source(system, machine), fetch=btbn_fetch(names))
                message = str(caught.exception)
                self.assertIn("layout", message)
                self.assertIn(route, message)

    def test_an_empty_or_unreadable_list_is_a_layout_change(self) -> None:
        for body in (b"", b"<html>not a checksum list</html>\n"):
            with self.subTest(body=body):
                fetch = FakeFetch({CHECKSUMS_URL: (body, None, None)})
                with self.assertRaises(InstallError) as caught:
                    resolve(pick_source("Windows", "AMD64"), fetch=fetch)
                self.assertIn("winget install Gyan.FFmpeg", str(caught.exception))

    def test_a_bad_checksum_line_for_the_chosen_name_is_refused(self) -> None:
        name = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip"
        body = f"{'ab' * 31}  {name}\n".encode()
        fetch = FakeFetch({CHECKSUMS_URL: (body, None, None), DOWNLOAD_FOLDER + name: (b"", None, 5)})
        with self.assertRaises(InstallError):
            resolve(pick_source("Windows", "AMD64"), fetch=fetch)

    def test_a_network_failure_names_the_host(self) -> None:
        with self.assertRaises(InstallError) as caught:
            resolve(pick_source("Windows", "AMD64"), fetch=refuse)
        message = str(caught.exception)
        self.assertIn("could not download ffmpeg from", message)
        self.assertIn(BTBN_HOST, message)
        self.assertIn("connection refused", message)
        self.assertIn("winget install Gyan.FFmpeg", message)

    def test_a_failure_while_asking_for_the_archive_size_is_an_install_error(self) -> None:
        name = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip"
        fetch = FakeFetch({CHECKSUMS_URL: (checksum_lines([name]), None, None)})
        with self.assertRaises(InstallError) as caught:
            resolve(pick_source("Windows", "AMD64"), fetch=fetch)
        self.assertIn(BTBN_HOST, str(caught.exception))

    def test_an_unknown_size_stays_unknown(self) -> None:
        release = resolve(
            pick_source("Linux", "x86_64"), fetch=btbn_fetch(BTBN_NAMES, size=None)
        )
        self.assertIsNone(release.archives[0].size)
        self.assertIsNone(release.size)


def mac_fetch(machine: str, *, probe_version: str = "9.0.2") -> FakeFetch:
    base = f"https://{MAC_HOST}"
    pages: dict[str, tuple] = {}
    for tool, version in (("ffmpeg", "9.0.2"), ("ffprobe", probe_version)):
        final = f"{base}/download/macos/{machine}/1789931890_{version}/{tool}.zip"
        pages[f"{base}/redirect/latest/macos/{machine}/release/{tool}.zip"] = (
            b"",
            final,
            (30_000_000 if tool == "ffmpeg" else 12_000_000),
        )
        pages[final + ".sha256"] = (f"{sha(tool + version)}  {tool}.zip\n".encode(), None, None)
    return FakeFetch(pages)


class ResolveMacTests(unittest.TestCase):
    def test_follows_the_redirect_and_reads_the_checksum_beside_the_final_url(self) -> None:
        for machine, key in (("arm64", "arm64"), ("x86_64", "amd64")):
            with self.subTest(machine=machine):
                source = pick_source("Darwin", machine)
                fetch = mac_fetch(key)
                release = resolve(source, fetch=fetch)
                self.assertEqual(release.version, "9.0.2")
                self.assertEqual(release.source, source)
                ffmpeg_archive, ffprobe_archive = release.archives
                final = f"https://{MAC_HOST}/download/macos/{key}/1789931890_9.0.2/"
                self.assertEqual(ffmpeg_archive.url, final + "ffmpeg.zip")
                self.assertEqual(ffprobe_archive.url, final + "ffprobe.zip")
                self.assertEqual(ffmpeg_archive.sha256, sha("ffmpeg9.0.2"))
                self.assertEqual(ffprobe_archive.sha256, sha("ffprobe9.0.2"))
                self.assertEqual((ffmpeg_archive.size, ffprobe_archive.size), (30_000_000, 12_000_000))
                self.assertEqual(release.size, 42_000_000)
                self.assertIn(final + "ffmpeg.zip.sha256", fetch.calls)
                self.assertIn(final + "ffprobe.zip.sha256", fetch.calls)

    def test_tools_that_disagree_on_the_version_are_refused(self) -> None:
        with self.assertRaises(InstallError) as caught:
            resolve(pick_source("Darwin", "arm64"), fetch=mac_fetch("arm64", probe_version="9.0.1"))
        self.assertIn("brew install ffmpeg", str(caught.exception))

    def test_a_checksum_file_in_another_shape_is_refused(self) -> None:
        fetch = mac_fetch("arm64")
        for url in list(fetch.pages):
            if url.endswith("ffmpeg.zip.sha256"):
                fetch.pages[url] = (b"<html>moved</html>\n", None, None)
        with self.assertRaises(InstallError):
            resolve(pick_source("Darwin", "arm64"), fetch=fetch)

    def test_a_redirect_without_a_version_in_its_path_is_refused(self) -> None:
        fetch = mac_fetch("arm64")
        base = f"https://{MAC_HOST}"
        fetch.pages[f"{base}/redirect/latest/macos/arm64/release/ffmpeg.zip"] = (
            b"",
            f"{base}/download/macos/arm64/latest/ffmpeg.zip",
            1000,
        )
        with self.assertRaises(InstallError) as caught:
            resolve(pick_source("Darwin", "arm64"), fetch=fetch)
        self.assertIn("brew install ffmpeg", str(caught.exception))

    def test_a_network_failure_names_the_host(self) -> None:
        with self.assertRaises(InstallError) as caught:
            resolve(pick_source("Darwin", "arm64"), fetch=refuse)
        self.assertIn("could not download ffmpeg from", str(caught.exception))
        self.assertIn(MAC_HOST, str(caught.exception))


class ReleaseSizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = pick_source("Darwin", "arm64")

    def test_size_is_the_sum_of_the_archives(self) -> None:
        release = Release(
            self.source, "1.0", (Archive("u1", "a.zip", "0" * 64, 10), Archive("u2", "b.zip", "0" * 64, 5))
        )
        self.assertEqual(release.size, 15)

    def test_size_is_unknown_when_any_archive_size_is_unknown(self) -> None:
        release = Release(
            self.source, "1.0", (Archive("u1", "a.zip", "0" * 64, 10), Archive("u2", "b.zip", "0" * 64, None))
        )
        self.assertIsNone(release.size)


# --- archives built in memory ----------------------------------------------------

TOP = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0"
FFMPEG_BYTES = b"ffmpeg program bytes"
FFPROBE_BYTES = b"ffprobe program bytes"


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def windows_entries(**replace: bytes | None) -> dict[str, bytes]:
    """A BtbN-style Windows layout: a top folder with the whole distribution."""
    entries = {
        f"{TOP}/bin/ffmpeg.exe": FFMPEG_BYTES,
        f"{TOP}/bin/ffprobe.exe": FFPROBE_BYTES,
        f"{TOP}/bin/ffplay.exe": b"ffplay",
        f"{TOP}/bin/avcodec-62.dll": b"dll one",
        f"{TOP}/bin/avutil-60.dll": b"dll two",
        f"{TOP}/bin/notes.txt": b"not a library",
        f"{TOP}/doc/ffmpeg.html": b"<html>",
        f"{TOP}/doc/": b"",
        f"{TOP}/include/libavcodec/avcodec.h": b"/* header */",
        f"{TOP}/lib/avcodec.lib": b"import library",
        f"{TOP}/lib/pkgconfig/libavcodec.pc": b"pkg",
        f"{TOP}/LICENSE.txt": b"license",
        f"{TOP}/presets/libvpx-720p.ffpreset": b"preset",
    }
    for name, value in replace.items():
        key = f"{TOP}/bin/{name}.exe"
        if value is None:
            entries.pop(key)
        else:
            entries[key] = value
    return entries


WINDOWS_KEPT = {
    "bin/ffmpeg.exe",
    "bin/ffprobe.exe",
    "bin/avcodec-62.dll",
    "bin/avutil-60.dll",
    "jzr-install.json",
}


def make_tar_xz(members: list[tuple]) -> bytes:
    """Members are (name, kind, payload): file/bytes, dir, symlink/target, chr."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(payload)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                archive.addfile(info)
            elif kind == "chr":
                info.type = tarfile.CHRTYPE
                archive.addfile(info)
            else:
                raise AssertionError(kind)
    return buffer.getvalue()


LTOP = "ffmpeg-n9.0-latest-linux64-gpl-shared-9.0"


def linux_members(*, symlinks: bool, extra: list[tuple] = ()) -> list[tuple]:  # type: ignore[assignment]
    members = [
        (LTOP, "dir", None),
        (f"{LTOP}/bin/ffmpeg", "file", FFMPEG_BYTES),
        (f"{LTOP}/bin/ffprobe", "file", FFPROBE_BYTES),
        (f"{LTOP}/bin/ffplay", "file", b"ffplay"),
        (f"{LTOP}/lib/libavcodec.so.62.3.100", "file", b"shared object"),
        (f"{LTOP}/lib/libavcodec.a", "file", b"static library"),
        (f"{LTOP}/lib/pkgconfig/libavcodec.pc", "file", b"pkg"),
        (f"{LTOP}/include/libavcodec/avcodec.h", "file", b"/* header */"),
        (f"{LTOP}/doc/ffmpeg.html", "file", b"<html>"),
        (f"{LTOP}/LICENSE.txt", "file", b"license"),
    ]
    if symlinks:
        members += [
            (f"{LTOP}/lib/libavcodec.so.62", "symlink", "libavcodec.so.62.3.100"),
            (f"{LTOP}/lib/libavcodec.so", "symlink", "libavcodec.so.62"),
        ]
    return members + list(extra)


def release_of(archives: list[tuple[str, bytes]], *, source=None, version: str = "9.0",
               declared_sha: str | None = None) -> tuple[Release, FakeFetch]:
    """A release for in-memory archives and a fetch that serves them."""
    source = source or pick_source("Windows", "AMD64")
    pages = {}
    entries = []
    for name, data in archives:
        url = f"https://downloads.test/{name}"
        pages[url] = (data, None, len(data))
        entries.append(Archive(url, name, declared_sha or sha(data), len(data)))
    return Release(source, version, tuple(entries)), FakeFetch(pages)


def snapshot(root: Path) -> dict[str, object]:
    """Every file, folder and link under ``root`` with its content or target."""
    found: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[key] = ("link", os.readlink(path))
        elif path.is_dir():
            found[key] = "dir"
        else:
            found[key] = path.read_bytes()
    return found


def files_in(folder: Path) -> set[str]:
    return {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() or p.is_symlink()
    }


class FakeVerify:
    """A stand-in for the "does the new copy run" check; records what it saw."""

    def __init__(self, version: str = "9.0", error: Exception | None = None,
                 watch: Path | None = None) -> None:
        self.version = version
        self.error = error
        self.watch = watch
        self.calls: list[dict[str, object]] = []

    def __call__(self, bin_dir: Path) -> str:
        bin_dir = Path(bin_dir)
        self.calls.append(
            {
                "bin": bin_dir,
                "files": sorted(os.listdir(bin_dir)),
                "watched_still_there": self.watch is not None and self.watch.exists(),
            }
        )
        if self.error is not None:
            raise self.error
        return self.version


class InstallTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.environ = {"JZR_DATA_DIR": str(self.data)}
        self.dest = self.data / "ffmpeg"

    def reset(self) -> None:
        """Forget a finished sub-case: remove the data folder it used."""
        shutil.rmtree(self.data, ignore_errors=True)

    def previous_install(self) -> Path:
        """Pre-create an install with a marker file and return the marker."""
        marker = self.dest / "bin" / "old-marker.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("old copy", encoding="utf-8")
        (self.dest / "jzr-install.json").write_text(
            json.dumps({"version": "1.0", "source": BTBN_HOST, "archives": [], "installed": "2020-01-01"}),
            encoding="utf-8",
        )
        return marker

    def run_install(self, release, fetch, *, platform="win32", verify=None, **kwargs):  # noqa: ANN001, ANN201
        return install(
            release,
            fetch=fetch,
            environ=self.environ,
            platform=platform,
            verify=verify or FakeVerify(),
            **kwargs,
        )

    def assert_install_fails(self, release, fetch, *, platform="win32", verify=None,  # noqa: ANN001
                             contains: str | None = None, previous: bool = True) -> None:
        marker = self.previous_install() if previous else None
        before = snapshot(self.root)
        with self.assertRaises(InstallError) as caught:
            self.run_install(release, fetch, platform=platform, verify=verify)
        if contains is not None:
            self.assertIn(contains, str(caught.exception))
        if marker is not None:
            self.assertEqual(snapshot(self.root), before, "files changed by a failed install")
            self.assertEqual(marker.read_text(encoding="utf-8"), "old copy")
            self.assertEqual(sorted(os.listdir(self.data)), ["ffmpeg"])
        else:
            self.assertEqual(sorted(os.listdir(self.data)) if self.data.exists() else [], [])
            self.assertIsNone(installed(self.environ))


class InstallLayoutTests(InstallTestCase):
    def test_a_windows_zip_keeps_the_programs_and_dlls_and_nothing_else(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))])
        self.run_install(release, fetch)
        self.assertEqual(files_in(self.dest), WINDOWS_KEPT)
        self.assertEqual((self.dest / "bin" / "ffmpeg.exe").read_bytes(), FFMPEG_BYTES)
        self.assertEqual((self.dest / "bin" / "ffprobe.exe").read_bytes(), FFPROBE_BYTES)
        self.assertEqual((self.dest / "bin" / "avcodec-62.dll").read_bytes(), b"dll one")
        for folder in ("doc", "include", "lib", "presets"):
            self.assertFalse((self.dest / folder).exists(), folder)

    def test_a_linux_tar_keeps_the_programs_and_shared_libraries_only(self) -> None:
        release, fetch = release_of(
            [("linux.tar.xz", make_tar_xz(linux_members(symlinks=False)))],
            source=pick_source("Linux", "x86_64"),
        )
        self.run_install(release, fetch, platform="linux")
        self.assertEqual(
            files_in(self.dest),
            {"bin/ffmpeg", "bin/ffprobe", "lib/libavcodec.so.62.3.100", "jzr-install.json"},
        )
        self.assertEqual((self.dest / "bin" / "ffmpeg").read_bytes(), FFMPEG_BYTES)
        if os.name != "nt":
            for name in ("ffmpeg", "ffprobe"):
                mode = stat.S_IMODE((self.dest / "bin" / name).stat().st_mode)
                self.assertEqual(mode, 0o755, name)

    @unittest.skipIf(os.name == "nt", "creating symbolic links needs extra rights on Windows")
    def test_a_linux_tar_keeps_library_symlinks_as_links(self) -> None:
        release, fetch = release_of(
            [("linux.tar.xz", make_tar_xz(linux_members(symlinks=True)))],
            source=pick_source("Linux", "x86_64"),
        )
        self.run_install(release, fetch, platform="linux")
        self.assertEqual(
            files_in(self.dest),
            {
                "bin/ffmpeg",
                "bin/ffprobe",
                "lib/libavcodec.so.62.3.100",
                "lib/libavcodec.so.62",
                "lib/libavcodec.so",
                "jzr-install.json",
            },
        )
        link = self.dest / "lib" / "libavcodec.so.62"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "libavcodec.so.62.3.100")
        self.assertEqual(link.read_bytes(), b"shared object")

    def test_a_macos_pair_of_bare_programs_lands_in_bin(self) -> None:
        release, fetch = release_of(
            [
                ("ffmpeg.zip", make_zip({"ffmpeg": FFMPEG_BYTES})),
                ("ffprobe.zip", make_zip({"ffprobe": FFPROBE_BYTES})),
            ],
            source=pick_source("Darwin", "arm64"),
        )
        self.run_install(release, fetch, platform="darwin")
        self.assertEqual(files_in(self.dest), {"bin/ffmpeg", "bin/ffprobe", "jzr-install.json"})
        self.assertEqual((self.dest / "bin" / "ffprobe").read_bytes(), FFPROBE_BYTES)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((self.dest / "bin" / "ffmpeg").stat().st_mode), 0o755)

    def test_the_data_folder_is_created_and_holds_only_ffmpeg_afterwards(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))])
        self.assertFalse(self.data.exists())
        self.run_install(release, fetch)
        self.assertEqual(sorted(os.listdir(self.data)), ["ffmpeg"])

    def test_the_new_copy_is_checked_in_a_separate_folder_before_the_swap(self) -> None:
        marker = self.previous_install()
        verify = FakeVerify(watch=marker)
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))])
        self.run_install(release, fetch, verify=verify)
        (call,) = verify.calls
        self.assertNotEqual(call["bin"], self.dest / "bin")
        self.assertIn("ffmpeg.exe", call["files"])
        self.assertIn("ffprobe.exe", call["files"])
        self.assertTrue(call["watched_still_there"], "old copy was gone before the check ran")

    def test_the_result_describes_the_install(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))])
        result = self.run_install(release, fetch)
        self.assertEqual(result.version, "9.0")
        self.assertEqual(result.source, BTBN_HOST)
        self.assertEqual(Path(result.directory), self.dest)


class InstallRecordTests(InstallTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.release, self.fetch = release_of([("win.zip", make_zip(windows_entries()))])

    def test_the_record_file_says_what_was_installed(self) -> None:
        self.run_install(self.release, self.fetch)
        record = json.loads((self.dest / "jzr-install.json").read_text(encoding="utf-8"))
        self.assertEqual(record["version"], "9.0")
        self.assertEqual(record["source"], BTBN_HOST)
        self.assertEqual(
            record["archives"],
            [{"name": "win.zip", "sha256": self.release.archives[0].sha256}],
        )
        datetime.date.fromisoformat(record["installed"][:10])

    def test_installed_reads_the_record(self) -> None:
        self.assertIsNone(installed(self.environ))
        self.run_install(self.release, self.fetch)
        found = installed(self.environ)
        self.assertIsNotNone(found)
        self.assertEqual(found.version, "9.0")
        self.assertEqual(found.source, BTBN_HOST)
        self.assertEqual(Path(found.directory), self.dest)

    def test_a_damaged_record_reads_as_not_installed(self) -> None:
        self.dest.mkdir(parents=True)
        for text in ("{not json", "[]", "{}", ""):
            with self.subTest(text=text):
                (self.dest / "jzr-install.json").write_text(text, encoding="utf-8")
                self.assertIsNone(installed(self.environ))

    def test_up_to_date_compares_archive_names_and_hashes(self) -> None:
        self.run_install(self.release, self.fetch)
        found = installed(self.environ)
        self.assertTrue(up_to_date(self.release, found))

        archive = self.release.archives[0]
        other_hash = Release(
            self.release.source, "9.0", (Archive(archive.url, archive.name, "f" * 64, archive.size),)
        )
        other_name = Release(
            self.release.source, "9.0", (Archive(archive.url, "other.zip", archive.sha256, archive.size),)
        )
        self.assertFalse(up_to_date(other_hash, found))
        self.assertFalse(up_to_date(other_name, found))

    def test_a_reinstall_replaces_the_old_copy(self) -> None:
        marker = self.previous_install()
        first, fetch_first = release_of([("win.zip", make_zip(windows_entries()))], version="1.5")
        self.run_install(first, fetch_first, verify=FakeVerify("1.5"))
        self.assertFalse(marker.exists())
        self.assertEqual(installed(self.environ).version, "1.5")

        (self.dest / "bin" / "second-marker.txt").write_text("from 1.5", encoding="utf-8")
        second, fetch_second = release_of(
            [("win2.zip", make_zip(windows_entries(ffmpeg=b"newer ffmpeg")))], version="2.0"
        )
        self.run_install(second, fetch_second, verify=FakeVerify("2.0"))
        self.assertFalse((self.dest / "bin" / "second-marker.txt").exists())
        self.assertEqual((self.dest / "bin" / "ffmpeg.exe").read_bytes(), b"newer ffmpeg")
        self.assertEqual(installed(self.environ).version, "2.0")
        self.assertEqual(sorted(os.listdir(self.data)), ["ffmpeg"])
        self.assertEqual(files_in(self.dest), WINDOWS_KEPT)


class InstallProgressTests(InstallTestCase):
    def test_progress_grows_and_ends_at_the_total(self) -> None:
        big = windows_entries(ffmpeg=os.urandom(3_000_000))
        data = make_zip(big)
        release, fetch = release_of([("win.zip", data)])
        calls: list[tuple[int, int | None]] = []
        self.run_install(release, fetch, on_progress=lambda done, total: calls.append((done, total)))
        self.assertGreaterEqual(len(calls), 2)
        counts = [done for done, _ in calls]
        self.assertEqual(counts, sorted(counts))
        self.assertGreater(len(set(counts)), 1)
        self.assertEqual({total for _, total in calls}, {len(data)})
        self.assertEqual(calls[-1], (len(data), len(data)))

    def test_progress_counts_across_several_archives(self) -> None:
        first = make_zip({"ffmpeg": os.urandom(400_000)})
        second = make_zip({"ffprobe": os.urandom(300_000)})
        release, fetch = release_of(
            [("ffmpeg.zip", first), ("ffprobe.zip", second)], source=pick_source("Darwin", "arm64")
        )
        calls: list[tuple[int, int | None]] = []
        self.run_install(
            release, fetch, platform="darwin", on_progress=lambda done, total: calls.append((done, total))
        )
        total = len(first) + len(second)
        counts = [done for done, _ in calls]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(calls[-1], (total, total))

    def test_progress_total_is_none_when_sizes_are_unknown(self) -> None:
        data = make_zip(windows_entries())
        release, fetch = release_of([("win.zip", data)])
        archive = release.archives[0]
        release = Release(
            release.source, release.version, (Archive(archive.url, archive.name, archive.sha256, None),)
        )
        calls: list[tuple[int, int | None]] = []
        self.run_install(release, fetch, on_progress=lambda done, total: calls.append((done, total)))
        self.assertEqual({total for _, total in calls}, {None})
        self.assertEqual(calls[-1][0], len(data))


class InstallFailureTests(InstallTestCase):
    """Every failure leaves the previous install and the data folder as they were."""

    def good(self) -> tuple[Release, FakeFetch]:
        return release_of([("win.zip", make_zip(windows_entries()))])

    def test_a_checksum_mismatch_names_the_archive(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))], declared_sha="0" * 64)
        self.assert_install_fails(release, fetch, contains="win.zip")

    def test_a_truncated_archive_installs_nothing(self) -> None:
        full = make_zip(windows_entries())
        for name, data in [
            ("zip", full[: len(full) // 2]),
            ("tar", make_tar_xz(linux_members(symlinks=False))[:200]),
        ]:
            with self.subTest(kind=name):
                release, fetch = release_of([(f"cut.{name}", data)])
                self.assert_install_fails(release, fetch, platform="linux" if name == "tar" else "win32")
                self.reset()

    def test_bytes_that_are_not_an_archive_install_nothing(self) -> None:
        release, fetch = release_of([("junk.zip", b"this is not an archive at all" * 50)])
        self.assert_install_fails(release, fetch)

    def test_a_download_that_fails_midway_installs_nothing(self) -> None:
        release, _ = self.good()

        def failing(url: str) -> FakeResponse:
            response = FakeResponse(url, b"", len(make_zip(windows_entries())))

            def read(n: int = -1) -> bytes:
                raise OSError("connection reset")

            response.read = read  # type: ignore[method-assign]
            return response

        self.assert_install_fails(release, failing)

    def test_zip_members_that_escape_the_folder_fail_the_whole_install(self) -> None:
        for bad in ("../evil", f"{TOP}/../../evil", "/evil", "C:/evil"):
            with self.subTest(member=bad):
                entries = windows_entries()
                entries[bad] = b"evil"
                release, fetch = release_of([("win.zip", make_zip(entries))])
                self.assert_install_fails(release, fetch, contains="win.zip")
                self.assertFalse((self.root / "evil").exists())
                self.reset()

    def test_tar_members_that_escape_the_folder_fail_the_whole_install(self) -> None:
        bad_members = {
            "parent folder": (f"{LTOP}/../../evil", "file", b"evil"),
            "parent at the start": ("../evil", "file", b"evil"),
            "absolute name": ("/tmp/jzr-evil", "file", b"evil"),
            "link out of the folder": (f"{LTOP}/lib/libx.so.1", "symlink", "../../../outside"),
            "absolute link": (f"{LTOP}/lib/libx.so.2", "symlink", "/etc/passwd"),
            "device file": (f"{LTOP}/lib/libx.so.3", "chr", None),
        }
        for label, member in bad_members.items():
            with self.subTest(case=label):
                release, fetch = release_of(
                    [("linux.tar.xz", make_tar_xz(linux_members(symlinks=False, extra=[member])))],
                    source=pick_source("Linux", "x86_64"),
                )
                self.assert_install_fails(release, fetch, platform="linux")
                self.assertFalse((self.root / "evil").exists())
                self.reset()

    def test_an_archive_without_ffprobe_installs_nothing(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries(ffprobe=None)))])
        self.assert_install_fails(release, fetch)

    def test_an_archive_without_ffmpeg_installs_nothing(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries(ffmpeg=None)))])
        self.assert_install_fails(release, fetch)

    def test_a_macos_pair_missing_one_program_installs_nothing(self) -> None:
        release, fetch = release_of(
            [
                ("ffmpeg.zip", make_zip({"ffmpeg": FFMPEG_BYTES})),
                ("ffprobe.zip", make_zip({"readme": b"no program here"})),
            ],
            source=pick_source("Darwin", "arm64"),
        )
        self.assert_install_fails(release, fetch, platform="darwin")

    def test_a_failing_check_installs_nothing_and_keeps_its_message(self) -> None:
        release, fetch = self.good()
        verify = FakeVerify(error=InstallError("the new ffmpeg does not run"))
        self.assert_install_fails(release, fetch, verify=verify, contains="the new ffmpeg does not run")
        self.assertEqual(len(verify.calls), 1)

    def test_a_failure_with_no_previous_install_leaves_nothing_behind(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))], declared_sha="1" * 64)
        self.assert_install_fails(release, fetch, previous=False)

    def test_a_network_error_installs_nothing(self) -> None:
        release, _ = self.good()
        self.assert_install_fails(release, refuse)

    def test_the_default_check_rejects_a_file_that_is_not_a_program(self) -> None:
        suffix = ".exe" if sys.platform == "win32" else ""
        entries = {
            f"{TOP}/bin/ffmpeg{suffix}": b"\x00\x01 junk, not an executable \xff",
            f"{TOP}/bin/ffprobe{suffix}": b"\x00\x01 junk, not an executable \xff",
        }
        release, fetch = release_of([("junk.zip", make_zip(entries))])
        marker = self.previous_install()
        before = snapshot(self.root)
        with self.assertRaises(InstallError):
            install(release, fetch=fetch, environ=self.environ, platform=sys.platform)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(marker.read_text(encoding="utf-8"), "old copy")
        self.assertEqual(sorted(os.listdir(self.data)), ["ffmpeg"])


class RemoveTests(InstallTestCase):
    def test_remove_deletes_the_install_folder(self) -> None:
        release, fetch = release_of([("win.zip", make_zip(windows_entries()))])
        self.run_install(release, fetch)
        self.assertTrue(remove(self.environ))
        self.assertFalse(self.dest.exists())
        self.assertIsNone(installed(self.environ))

    def test_remove_says_false_when_nothing_was_there(self) -> None:
        self.assertFalse(remove(self.environ))
        self.data.mkdir()
        self.assertFalse(remove(self.environ))

    def test_removing_twice_reports_true_then_false(self) -> None:
        self.previous_install()
        self.assertTrue(remove(self.environ))
        self.assertFalse(remove(self.environ))


# --- startup -------------------------------------------------------------------


class ImportTests(unittest.TestCase):
    def test_importing_the_installer_does_not_import_the_network_stack(self) -> None:
        import subprocess

        src = Path(__file__).resolve().parents[1] / "src"
        code = (
            "import sys\n"
            "import jzr_tools.core.ffmpeg_install\n"
            "print('urllib.request' in sys.modules)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONPATH": str(src)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "False")


# --- the real sources (opt in) ---------------------------------------------------


@unittest.skipUnless(os.environ.get("JZR_TEST_NETWORK") == "1", "set JZR_TEST_NETWORK=1 to ask the real sources")
class RealSourcesTests(unittest.TestCase):
    def test_every_source_publishes_a_version_checksum_and_size(self) -> None:
        for source in all_sources():
            with self.subTest(system=source.system, machine=source.machine):
                release = resolve(source, fetch=ffmpeg_install.urlopen_fetch)
                self.assertRegex(release.version, r"^\d+(\.\d+)+$")
                self.assertTrue(release.archives)
                for archive in release.archives:
                    self.assertRegex(archive.sha256, r"^[0-9a-f]{64}$")
                    self.assertIsInstance(archive.size, int)
                    self.assertGreater(archive.size, 10_000_000)


if __name__ == "__main__":
    unittest.main()
