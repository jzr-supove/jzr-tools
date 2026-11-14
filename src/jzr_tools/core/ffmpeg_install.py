"""Download ffmpeg and ffprobe into jzr's own per-user folder.

Nothing outside that folder changes: no admin rights, no PATH edits. Only
``resolve`` and ``install`` use the network, and only through the ``fetch``
function they are given, so everything else here works offline.

An install never leaves jzr worse off. Each archive is checked against the
SHA-256 its source publishes, unpacked into ``ffmpeg-new``, run once, and
only then swapped with the old copy. Any failure removes what it made and
leaves the old copy as it was.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Protocol

__all__ = [
    "SOURCES",
    "Archive",
    "InstallError",
    "Installed",
    "Release",
    "Response",
    "Source",
    "data_dir",
    "install",
    "install_dir",
    "installed",
    "manual_route",
    "pick_source",
    "remove",
    "resolve",
    "tool_path",
    "up_to_date",
    "urlopen_fetch",
]

_TOOLS = ("ffmpeg", "ffprobe")
_RECORD = "jzr-install.json"
_CHUNK = 1 << 16


class InstallError(Exception):
    """An install that could not be done; the text is meant for the user."""


# --- where jzr keeps its copy --------------------------------------------------


def data_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return jzr's per-user data folder, whether or not it exists."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if environ.get("JZR_DATA_DIR"):
        return Path(environ["JZR_DATA_DIR"])
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    if platform == "win32":
        local = environ.get("LOCALAPPDATA")
        return (Path(local) if local else home / "AppData" / "Local") / "jzr"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "jzr"
    configured = environ.get("XDG_DATA_HOME", "")
    # The XDG rules say a relative path here is to be ignored.
    base = Path(configured) if os.path.isabs(configured) else home / ".local" / "share"
    return base / "jzr"


def install_dir(environ: Mapping[str, str] | None = None, platform: str | None = None) -> Path:
    """Return the folder ``jzr ffmpeg install`` puts ffmpeg in."""
    return data_dir(environ, platform) / "ffmpeg"


def _program(name: str, platform: str) -> str:
    return f"{name}.exe" if platform == "win32" else name


def tool_path(
    name: str, environ: Mapping[str, str] | None = None, platform: str | None = None
) -> Path:
    """Return where jzr's copy of ``name`` lives, whether or not it is there."""
    platform = sys.platform if platform is None else platform
    return install_dir(environ, platform) / "bin" / _program(name, platform)


# --- where builds come from ------------------------------------------------------


@dataclass(frozen=True)
class Source:
    system: str
    machine: str
    # Where the download comes from, as the question shows it.
    host: str
    kind: str
    # The platform's name in the source's file names or URLs.
    key: str


_BTBN = "github.com/BtbN/FFmpeg-Builds"
_BTBN_FOLDER = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
_RIEDL = "ffmpeg.martin-riedl.de"
_RIEDL_LATEST = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{key}/release/{tool}.zip"

SOURCES: dict[tuple[str, str], Source] = {
    ("windows", "x64"): Source("windows", "x64", _BTBN, "btbn", "win64"),
    ("windows", "arm64"): Source("windows", "arm64", _BTBN, "btbn", "winarm64"),
    ("linux", "x64"): Source("linux", "x64", _BTBN, "btbn", "linux64"),
    ("linux", "arm64"): Source("linux", "arm64", _BTBN, "btbn", "linuxarm64"),
    ("macos", "arm64"): Source("macos", "arm64", _RIEDL, "martin-riedl", "arm64"),
    ("macos", "x64"): Source("macos", "x64", _RIEDL, "martin-riedl", "amd64"),
}

_SYSTEMS = {"windows": "windows", "linux": "linux", "darwin": "macos"}
_MACHINES = {
    "x86_64": "x64",
    "amd64": "x64",
    "x64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def pick_source(system: str, machine: str) -> Source | None:
    """Return the build for ``platform.system()`` and ``platform.machine()``."""
    system_key = _SYSTEMS.get(system.lower())
    machine_key = _MACHINES.get(machine.lower())
    if system_key is None or machine_key is None:
        return None
    return SOURCES.get((system_key, machine_key))


def manual_route(system: str) -> str:
    """Return the usual way to install ffmpeg by hand on ``system``."""
    lowered = system.lower()
    if lowered == "windows":
        return "winget install Gyan.FFmpeg"
    if lowered == "darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg"


_SYSTEM_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "Darwin"}


def _yourself(source: Source) -> str:
    return f"Install it yourself: {manual_route(_SYSTEM_NAMES[source.system])}"


# --- downloading -------------------------------------------------------------------


class Response(Protocol):
    """What ``fetch`` returns: an open download."""

    url: str
    size: int | None

    def read(self, n: int = -1) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *exc: object) -> object: ...


Fetch = Callable[[str], Response]


class _UrllibResponse:
    def __init__(self, raw: object) -> None:
        self._raw = raw
        self.url: str = raw.geturl()  # type: ignore[attr-defined]
        length = raw.headers.get("Content-Length")  # type: ignore[attr-defined]
        self.size: int | None = int(length) if length and length.isdigit() else None

    def read(self, n: int = -1) -> bytes:
        return self._raw.read(n)  # type: ignore[attr-defined]

    def __enter__(self) -> _UrllibResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self._raw.close()  # type: ignore[attr-defined]


def urlopen_fetch(url: str) -> _UrllibResponse:
    """Open ``url``: the one place jzr goes to the network."""
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "jzr"})
    return _UrllibResponse(urllib.request.urlopen(request, timeout=30))


@dataclass(frozen=True)
class Archive:
    url: str
    name: str
    sha256: str
    size: int | None


@dataclass(frozen=True)
class Release:
    source: Source
    version: str
    archives: tuple[Archive, ...]

    @property
    def size(self) -> int | None:
        """Bytes to download, or ``None`` when any archive's size is unknown."""
        total = 0
        for archive in self.archives:
            if archive.size is None:
                return None
            total += archive.size
        return total


def _reason(error: OSError) -> str:
    reason = getattr(error, "reason", None)
    if reason is not None:
        return str(reason)
    return error.strerror or str(error)


def _network_error(source: Source, error: OSError) -> InstallError:
    return InstallError(
        f"could not download ffmpeg from {source.host}: {_reason(error)}. {_yourself(source)}"
    )


def _layout_error(source: Source, detail: str) -> InstallError:
    return InstallError(
        f"{source.host} has changed the layout of its downloads ({detail}), "
        f"so jzr cannot install ffmpeg from it. {_yourself(source)}"
    )


_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION = r"\d+(?:\.\d+)*"


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _resolve_btbn(source: Source, fetch: Fetch) -> Release:
    with fetch(_BTBN_FOLDER + "checksums.sha256") as response:
        listing = response.read().decode("utf-8", errors="replace")
    pattern = re.compile(
        rf"^ffmpeg-n({_VERSION})-latest-{re.escape(source.key)}-gpl-shared-({_VERSION})"
        r"\.(?:zip|tar\.xz)$"
    )
    best: tuple[tuple[int, ...], str, str] | None = None
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        name = fields[1].lstrip("*")
        match = pattern.match(name)
        if match is None or match.group(1) != match.group(2):
            continue
        key = _version_key(match.group(1))
        if best is None or key > best[0]:
            best = (key, match.group(1), line)
    if best is None:
        raise _layout_error(source, f"no {source.key} gpl-shared build in checksums.sha256")
    _, version, line = best
    digest, name = line.split()
    name = name.lstrip("*")
    if not _HEX.match(digest):
        raise _layout_error(source, f"no readable checksum for {name}")
    url = _BTBN_FOLDER + name
    with fetch(url) as response:
        size = response.size
    return Release(source, version, (Archive(url, name, digest.lower(), size),))


_RIEDL_VERSION = re.compile(rf"/\d+_({_VERSION})/[^/]+$")


def _resolve_riedl(source: Source, fetch: Fetch) -> Release:
    archives = []
    versions = set()
    for tool in _TOOLS:
        with fetch(_RIEDL_LATEST.format(key=source.key, tool=tool)) as response:
            final, size = response.url, response.size
        match = _RIEDL_VERSION.search(final)
        if match is None:
            raise _layout_error(source, f"no version in {final}")
        versions.add(match.group(1))
        with fetch(final + ".sha256") as response:
            fields = response.read().decode("utf-8", errors="replace").split()
        if not fields or not _HEX.match(fields[0]):
            raise _layout_error(source, f"no readable checksum for {tool}.zip")
        archives.append(Archive(final, posixpath.basename(final), fields[0].lower(), size))
    if len(versions) != 1:
        raise _layout_error(source, "ffmpeg and ffprobe have different versions")
    return Release(source, versions.pop(), tuple(archives))


def resolve(source: Source, *, fetch: Fetch) -> Release:
    """Find the newest build ``source`` offers, with its checksums and sizes."""
    try:
        if source.kind == "btbn":
            return _resolve_btbn(source, fetch)
        return _resolve_riedl(source, fetch)
    except OSError as error:
        raise _network_error(source, error) from error


def _download(
    archive: Archive,
    destination: Path,
    *,
    fetch: Fetch,
    progress: Callable[[int], None],
) -> None:
    digest = hashlib.sha256()
    with fetch(archive.url) as response, open(destination, "wb") as sink:
        while True:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            sink.write(chunk)
            progress(len(chunk))
    if digest.hexdigest() != archive.sha256.lower():
        raise InstallError(
            f"the download of {archive.name} does not match the SHA-256 checksum its "
            "source publishes, so nothing was installed. Try again later."
        )


# --- unpacking ---------------------------------------------------------------------


def _outside(archive: str, member: str) -> InstallError:
    return InstallError(
        f"{archive} tries to write outside its folder ({member}), so nothing was installed."
    )


def _parts(archive: str, name: str) -> tuple[str, ...]:
    """Split a member name, refusing any name that leaves the folder."""
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name) or "\\" in name:
        raise _outside(archive, name)
    parts = tuple(part for part in PurePosixPath(name).parts if part not in ("", "."))
    if ".." in parts:
        raise _outside(archive, name)
    return parts


def _top_folder(names: list[tuple[str, ...]]) -> int:
    """Return 1 when every member sits inside one top folder, else 0."""
    tops = {parts[0] for parts in names if parts}
    if len(tops) == 1 and any(len(parts) > 1 for parts in names):
        return 1
    return 0


def _kept(parts: tuple[str, ...], platform: str) -> tuple[str, str] | None:
    """Return (``bin`` or ``lib``, file name) for a member worth keeping."""
    programs = {_program(tool, platform) for tool in _TOOLS}
    if len(parts) == 1:
        return ("bin", parts[0]) if parts[0] in programs else None
    if len(parts) != 2:
        return None
    folder, name = parts
    if folder == "bin" and (name in programs or name.lower().endswith(".dll")):
        return "bin", name
    if folder == "lib" and re.search(r"\.so(?:\.\d+)*$", name):
        return "lib", name
    return None


def _unpack_zip(path: Path, label: str, target: Path, platform: str) -> None:
    import zipfile

    with zipfile.ZipFile(path) as archive:
        members = [(info, _parts(label, info.filename)) for info in archive.infolist()]
        skip = _top_folder([parts for _, parts in members])
        for info, parts in members:
            if info.is_dir():
                continue
            kept = _kept(parts[skip:], platform)
            if kept is None:
                continue
            destination = target / kept[0] / kept[1]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink, _CHUNK)


def _unpack_tar(path: Path, label: str, target: Path, platform: str) -> None:
    import tarfile

    data_filter = getattr(tarfile, "data_filter", None)
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        parted = []
        # Check every member before writing anything.
        for member in members:
            parts = _parts(label, member.name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise _outside(label, member.name)
            if member.issym():
                link = member.linkname
                joined = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), link))
                if link.startswith("/") or joined == ".." or joined.startswith("../"):
                    raise _outside(label, f"{member.name} -> {link}")
            if data_filter is not None:
                try:
                    data_filter(member, str(target))
                except tarfile.FilterError as error:
                    raise _outside(label, member.name) from error
            parted.append((member, parts))

        skip = _top_folder([parts for _, parts in parted])
        links = []
        for member, parts in parted:
            if member.isdir():
                continue
            kept = _kept(parts[skip:], platform)
            if kept is None:
                continue
            destination = target / kept[0] / kept[1]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                links.append((destination, member.linkname))
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink, _CHUNK)

    # Links last, so a copy can stand in where links cannot be made.
    for destination, link in links:
        if "/" in link:
            continue  # only links to a sibling library are kept
        try:
            os.symlink(link, destination)
        except OSError:
            sibling = destination.parent / link
            if sibling.exists():
                shutil.copyfile(sibling, destination)


def _unpack(path: Path, archive: Archive, target: Path, platform: str) -> None:
    import lzma
    import tarfile
    import zipfile
    import zlib

    try:
        if zipfile.is_zipfile(path):
            _unpack_zip(path, archive.name, target, platform)
        else:
            _unpack_tar(path, archive.name, target, platform)
    except (zipfile.BadZipFile, tarfile.TarError, lzma.LZMAError, zlib.error, EOFError) as error:
        raise InstallError(
            f"{archive.name} is not a readable archive ({error}), so nothing was installed."
        ) from error


# --- checking and swapping -------------------------------------------------------------


def _first_line(data: bytes) -> str:
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[0] if lines else ""


def _default_verify(bin_dir: Path) -> str:
    """Run the new ffmpeg and ffprobe once; return ffmpeg's version."""
    version = "unknown"
    for tool in _TOOLS:
        path = bin_dir / _program(tool, sys.platform)
        try:
            completed = subprocess.run(
                [str(path), "-version"], capture_output=True, timeout=60, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallError(
                f"the downloaded {tool} does not run on this machine ({error}), "
                "so nothing was installed."
            ) from error
        words = _first_line(completed.stdout).split()
        if completed.returncode != 0 or words[:2] != [tool, "version"]:
            raise InstallError(
                f"the downloaded {tool} does not run on this machine "
                f"(exit code {completed.returncode}), so nothing was installed."
            )
        if tool == "ffmpeg" and len(words) >= 3:
            version = words[2]
    return version


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _swap(new: Path, final: Path, old: Path) -> None:
    had_old = final.exists()
    try:
        if had_old:
            os.rename(final, old)
    except OSError as error:
        raise InstallError(
            f"could not replace {final} ({_reason(error)}). Close any program using "
            "jzr's ffmpeg and try again."
        ) from error
    try:
        os.rename(new, final)
    except OSError as error:
        if had_old:
            os.rename(old, final)
        raise InstallError(f"could not move the new ffmpeg into {final} ({_reason(error)}).") from error
    _remove_tree(old)


@dataclass(frozen=True)
class Installed:
    version: str
    # The host it came from.
    source: str
    directory: Path
    # (name, sha256) of each archive it was made from.
    archives: tuple[tuple[str, str], ...]


def install(
    release: Release,
    *,
    fetch: Fetch,
    on_progress: Callable[[int, int | None], None] | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    verify: Callable[[Path], str] | None = None,
) -> Installed:
    """Download, check, unpack and swap in ``release``; raise ``InstallError``."""
    platform = sys.platform if platform is None else platform
    root = data_dir(environ, platform)
    final = root / "ffmpeg"
    new = root / "ffmpeg-new"
    old = root / "ffmpeg-old"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InstallError(f"could not create {root} ({_reason(error)}).") from error

    # A run that stopped between the two renames left the good copy here.
    if old.exists() and not final.exists():
        os.rename(old, final)
    _remove_tree(new)
    _remove_tree(old)

    downloads: list[Path] = []
    done = 0
    total = release.size

    def progress(count: int) -> None:
        nonlocal done
        done += count
        if on_progress is not None:
            on_progress(done, total)

    try:
        for archive in release.archives:
            handle, name = tempfile.mkstemp(prefix="jzr-download-", suffix=".part", dir=root)
            os.close(handle)
            path = Path(name)
            downloads.append(path)
            try:
                _download(archive, path, fetch=fetch, progress=progress)
            except OSError as error:
                raise _network_error(release.source, error) from error

        new.mkdir()
        for archive, path in zip(release.archives, downloads):
            _unpack(path, archive, new, platform)
        for tool in _TOOLS:
            program = new / "bin" / _program(tool, platform)
            if not program.is_file():
                raise _layout_error(release.source, f"no {program.name} in the download")
            if platform != "win32":
                program.chmod(0o755)

        (verify or _default_verify)(new / "bin")

        record = {
            "version": release.version,
            "source": release.source.host,
            "archives": [{"name": a.name, "sha256": a.sha256} for a in release.archives],
            "installed": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        (new / _RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8")
        _swap(new, final, old)
    except OSError as error:
        raise InstallError(f"could not install ffmpeg into {final} ({_reason(error)}).") from error
    finally:
        _remove_tree(new)
        for path in downloads:
            path.unlink(missing_ok=True)

    return Installed(
        release.version,
        release.source.host,
        final,
        tuple((a.name, a.sha256) for a in release.archives),
    )


def installed(
    environ: Mapping[str, str] | None = None, platform: str | None = None
) -> Installed | None:
    """Describe jzr's copy from its record, or ``None`` when there is none."""
    directory = install_dir(environ, platform)
    try:
        record = json.loads((directory / _RECORD).read_text(encoding="utf-8"))
        version = record["version"]
        source = record["source"]
        archives = tuple((item["name"], item["sha256"]) for item in record["archives"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(version, str) or not isinstance(source, str):
        return None
    return Installed(version, source, directory, archives)


def up_to_date(release: Release, current: Installed | None) -> bool:
    """Whether ``current`` was made from exactly the archives of ``release``."""
    if current is None:
        return False
    return current.archives == tuple((a.name, a.sha256) for a in release.archives)


def remove(environ: Mapping[str, str] | None = None, platform: str | None = None) -> bool:
    """Delete jzr's copy; ``False`` when there was nothing to delete."""
    root = data_dir(environ, platform)
    for leftover in ("ffmpeg-new", "ffmpeg-old"):
        _remove_tree(root / leftover)
    directory = root / "ffmpeg"
    if not directory.exists():
        return False
    try:
        shutil.rmtree(directory)
    except OSError as error:
        raise InstallError(f"could not remove {directory} ({_reason(error)}).") from error
    return True
