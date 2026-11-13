"""Find and remove caches and other disposable data.

``scan`` lists what could be removed and how much each item would give back;
``clean`` removes one item. Neither decides what the user wants gone.

Linux and Windows keep their caches in different places and offer different
items; ``_windows`` decides which list applies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

MINIMUM_BYTES = 10 * 1024**2
# Unknown cache directories are only worth a line when they are large.
MINIMUM_OTHER_BYTES = 100 * 1024**2
JOURNAL_KEEP_BYTES = 500 * 1024**2
TIERS = ("safe", "review", "root")

DNF_CACHE_PATHS = (Path("/var/cache/libdnf5"), Path("/var/cache/dnf"))
APT_CACHE_PATHS = (Path("/var/cache/apt/archives"),)

_QUERY_TIMEOUT = 30

# id -> (title, directory names under the cache directory, what removing costs)
_CACHE_DIRECTORIES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "thumbnails": ("Thumbnails", ("thumbnails",), "File managers draw them again."),
    "go-build": ("Go build cache", ("go-build",), "The next build compiles everything again."),
    "yarn": ("Yarn cache", ("yarn",), "Packages are downloaded again when needed."),
    "pnpm": ("pnpm metadata cache", ("pnpm",), "Package metadata is fetched again."),
    "node-gyp": ("node-gyp headers", ("node-gyp",), "Headers are downloaded again."),
    "mesa-shaders": (
        "Mesa shader cache",
        ("mesa_shader_cache", "mesa_shader_cache_db"),
        "Applications compile their shaders again.",
    ),
}
# id -> the variable that moves that cache elsewhere, on every system
_CACHE_VARIABLES = {"go-build": "GOCACHE", "yarn": "YARN_CACHE_FOLDER"}

# Below %LOCALAPPDATA%: where the graphics drivers keep compiled shaders.
_WINDOWS_SHADER_CACHES = (
    ("NVIDIA", "DXCache"),
    ("NVIDIA", "GLCache"),
    ("D3DSCache",),
    ("AMD", "DxCache"),
    ("AMD", "DxcCache"),
    ("AMD", "VkCache"),
    ("AMD", "GLCache"),
    ("Intel", "ShaderCache"),
)

# Docker's name for a kind of data -> (id, tier, title, command, what removing costs)
_DOCKER: dict[str, tuple[str, str, str, tuple[str, ...], str]] = {
    "Build Cache": (
        "docker-build-cache",
        "safe",
        "Docker build cache",
        ("docker", "builder", "prune", "--all", "--force"),
        "The next image builds start from scratch.",
    ),
    "Containers": (
        "docker-containers",
        "review",
        "Stopped Docker containers",
        ("docker", "container", "prune", "--force"),
        "Anything written inside those containers is lost.",
    ),
    "Images": (
        "docker-images",
        "review",
        "Docker images no container uses",
        ("docker", "image", "prune", "--all", "--force"),
        "They are pulled or built again when needed.",
    ),
}

# Every id either system can report, so that --only means the same everywhere.
KNOWN_IDS: tuple[str, ...] = (
    "docker-build-cache",
    "uv",
    "pip",
    "npm",
    "cargo",
    *_CACHE_DIRECTORIES,
    "gpu-shaders",
    "trash",
    "temp",
    "crash-dumps",
    "docker-containers",
    "docker-images",
    "journal",
    "dnf",
    "apt",
    "windows-temp",
    "windows-update",
)
_OTHER_PREFIX = "cache:"

_DECIMAL_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
_BINARY_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_DOCKER_SIZE = re.compile(r"([\d.]+)\s*([kKMGT]?B)")
_JOURNAL_SIZE = re.compile(r"take up ([\d.]+)\s*([BKMGT])")

# Windows refuses to delete a file another program has open: a sharing or
# lock violation, or "access denied" for a running program's own files.
_IN_USE_ERRORS = frozenset({32, 33, 5})
_ACCESS_DENIED = 5
_FILE_ATTRIBUTE_READONLY = 0x1
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

DOWNLOADED_AGAIN = "Packages are downloaded again when needed."
BUILT_AGAIN = "Packages are downloaded and built again when needed."
TRASH_NOTE = "Files you deleted can no longer be restored."


def _windows() -> bool:
    """Return whether this is Windows; asked at run time so tests can fake it."""
    return sys.platform == "win32"


@dataclass(frozen=True)
class Item:
    """Something that can be removed, and how."""

    id: str
    title: str
    tier: str
    size_bytes: int
    note: str
    paths: tuple[Path, ...] = ()  # directories to empty
    command: tuple[str, ...] = ()  # or the command that removes it; wins over paths

    @property
    def action(self) -> str:
        """The command line, or the directories that would be emptied."""
        if self.command:
            if _windows():
                return subprocess.list2cmdline(self.command)
            return " ".join(self.command)
        if not self.paths and self.id == "trash":
            return "the Recycle Bin of every drive"
        return ", ".join(str(path) for path in self.paths)


@dataclass(frozen=True)
class Outcome:
    """What removing one item did."""

    item: Item
    ok: bool
    freed_bytes: int
    error: str = ""
    in_use: int = 0  # files left in place because a program held them open


def is_known_id(item_id: str) -> bool:
    """Return whether ``item_id`` names a kind of item ``scan`` can report."""
    return item_id in KNOWN_IDS or (
        item_id.startswith(_OTHER_PREFIX) and len(item_id) > len(_OTHER_PREFIX)
    )


def _resolved(command: Sequence[str]) -> list[str]:
    """Return ``command`` with its program as a full path on Windows.

    Windows starts a program by name only if it is an ``.exe``; tools such as
    npm are ``.cmd`` files, which have to be named in full.
    """
    words = list(command)
    if words and _windows():
        words[0] = shutil.which(words[0]) or words[0]
    return words


def _capture(command: list[str]) -> str | None:
    """Run a read-only query and return its output, or None if it failed."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_QUERY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _execute(command: list[str]) -> tuple[int, str]:
    """Run a removing command; return its exit code and its last line of output.

    Standard input is left alone so ``sudo`` can ask for a password.
    """
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return 1, exc.strerror or str(exc)
    lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
    return result.returncode, lines[-1].strip() if lines else ""


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _is_elevated() -> bool:
    """Return whether this process runs as an elevated Windows administrator."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _sudo_enabled() -> bool:
    """Return whether Windows sudo is switched on, in any of its modes."""
    try:
        import winreg

        key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Sudo"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as opened:  # type: ignore[attr-defined]
            value, _ = winreg.QueryValueEx(opened, "Enabled")  # type: ignore[attr-defined]
    except (ImportError, OSError):
        return False
    return isinstance(value, int) and value > 0


def _recycle_bin_info() -> object:
    """Return an empty ``SHQUERYRBINFO``, laid out as the shell expects."""
    import ctypes

    fields = {
        "_fields_": [
            ("cbSize", ctypes.c_uint32),
            ("i64Size", ctypes.c_int64),
            ("i64NumItems", ctypes.c_int64),
        ]
    }
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        fields["_pack_"] = 1  # the 32-bit headers pack this structure
    info = type("SHQUERYRBINFO", (ctypes.Structure,), fields)()
    info.cbSize = ctypes.sizeof(info)
    return info


def _recycle_bin_size() -> int:
    """Return the bytes held in the Recycle Bin of every drive."""
    try:
        import ctypes

        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        shell32.SHQueryRecycleBinW.restype = ctypes.c_long
        info = _recycle_bin_info()
        if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return 0
    except (AttributeError, OSError):
        return 0
    return max(0, int(info.i64Size))  # type: ignore[attr-defined]


def _empty_recycle_bin() -> None:
    """Empty the Recycle Bin of every drive, without dialogs or sounds."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    shell32.SHEmptyRecycleBinW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD)
    shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
    no_confirmation, no_progress, no_sound = 0x1, 0x2, 0x4
    result = shell32.SHEmptyRecycleBinW(None, None, no_confirmation | no_progress | no_sound)
    # The call also fails when the bin is already empty, which is no failure.
    if result != 0 and _recycle_bin_size() > 0:
        raise OSError(f"The Recycle Bin could not be emptied (0x{result & 0xFFFFFFFF:08X}).")


def _is_link(status: os.stat_result) -> bool:
    """Return whether a Windows entry is a link or junction, which is never entered."""
    return bool(getattr(status, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def directory_size(path: Path) -> int:
    """Return the disk space used by everything inside ``path``.

    Hard-linked files count once, links are not followed, and whatever cannot
    be read is left out. Windows does not report link counts while listing a
    directory, and asking for each file costs ten times as long, so there a
    hard-linked file counts once per link.
    """
    total = 0
    seen: set[tuple[int, int]] = set()
    pending = [os.fspath(path)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                found = list(entries)
        except OSError:
            continue
        for entry in found:
            try:
                status = entry.stat(follow_symlinks=False)
                if entry.is_dir(follow_symlinks=False) and not _is_link(status):
                    pending.append(entry.path)
            except OSError:
                continue
            if status.st_nlink > 1:
                link = (status.st_dev, status.st_ino)
                if link in seen:
                    continue
                seen.add(link)
            blocks = getattr(status, "st_blocks", None)
            total += status.st_size if blocks is None else blocks * 512
    return total


def _refusal(path: Path, home: Path) -> str:
    """Return why ``path`` must not be emptied, or an empty string."""
    resolved = Path(os.path.realpath(path))
    home = Path(os.path.realpath(home))
    if not resolved.is_dir():
        return "not a directory"
    if resolved == home or resolved in home.parents:
        return "refusing to empty the home directory or a directory that holds it"
    if len(resolved.parts) < 4:
        return "refusing to empty a directory this close to the root"
    return ""


def _remove_tree(path: str, errors: list[str]) -> None:
    def note(_function: object, failed: str, error: object) -> None:
        if isinstance(error, tuple):  # the pre-3.12 handler gets exc_info
            error = error[1]
        errors.append(f"{failed}: {getattr(error, 'strerror', None) or error}")

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=note)
    else:
        shutil.rmtree(path, onerror=note)


def _in_use(error: OSError) -> bool:
    return _windows() and getattr(error, "winerror", None) in _IN_USE_ERRORS


def _make_writable(path: str) -> bool:
    """Clear a Windows read-only flag; return whether there was one."""
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if not attributes & _FILE_ATTRIBUTE_READONLY:
            return False
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        return False
    return True


def _delete(remove: Callable[[str], None], path: str, errors: list[str]) -> int:
    """Remove one file, link or empty directory; return 1 if it was in use."""
    try:
        remove(path)
        return 0
    except FileNotFoundError:
        return 0
    except OSError as exc:
        error = exc
    if getattr(error, "winerror", None) == _ACCESS_DENIED and _make_writable(path):
        try:
            remove(path)
            return 0
        except OSError as exc:
            error = exc
    if _in_use(error):
        return 1
    errors.append(f"{path}: {error.strerror or error}")
    return 0


def _clear_windows(path: str, errors: list[str]) -> int:
    """Remove what is inside ``path``, keeping files a program holds open.

    Returns how many were kept. A directory that still holds kept files is
    kept too, and that is no error.
    """
    try:
        with os.scandir(path) as entries:
            found = list(entries)
    except OSError as exc:
        if _in_use(exc):
            return 1
        errors.append(f"{path}: {exc.strerror or exc}")
        return 0
    kept = 0
    for entry in found:
        try:
            status = entry.stat(follow_symlinks=False)
            is_directory = entry.is_dir(follow_symlinks=False) and not _is_link(status)
        except OSError as exc:
            errors.append(f"{entry.path}: {exc.strerror or exc}")
            continue
        if is_directory:
            inside = _clear_windows(entry.path, errors)
            kept += inside or _delete(os.rmdir, entry.path, errors)
        else:
            # os.unlink also removes a link or junction to a directory, and
            # never what it points at.
            kept += _delete(os.unlink, entry.path, errors)
    return kept


def _empty(path: Path, home: Path | None = None) -> tuple[list[str], int]:
    """Empty ``path``; return the errors and how many files in use were kept."""
    refusal = _refusal(path, Path.home() if home is None else home)
    if refusal:
        return [f"{path}: {refusal}"], 0

    errors: list[str] = []
    if _windows():
        kept = _clear_windows(os.fspath(path), errors)
        return errors, kept

    try:
        with os.scandir(path) as entries:
            found = list(entries)
    except OSError as exc:
        return [f"{path}: {exc.strerror or exc}"], 0
    for entry in found:
        try:
            if entry.is_dir(follow_symlinks=False):
                _remove_tree(entry.path, errors)
            else:
                os.unlink(entry.path)
        except OSError as exc:
            errors.append(f"{entry.path}: {exc.strerror or exc}")
    return errors, 0


def empty_directory(path: Path, *, home: Path | None = None) -> list[str]:
    """Remove everything inside ``path`` and keep the directory itself.

    Returns one message per thing that could not be removed. Links inside are
    removed as links; what they point at is not touched. On Windows, files
    another program holds open are left in place without a message.
    """
    return _empty(path, home)[0]


def _docker_reclaimable() -> dict[str, int] | None:
    """Return what Docker says each kind of data would give back, by item id."""
    if shutil.which("docker") is None:
        return None
    output = _capture(_resolved(["docker", "system", "df", "--format", "{{json .}}"]))
    if output is None:
        return None
    sizes: dict[str, int] = {}
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("Type") not in _DOCKER:
            continue
        match = _DOCKER_SIZE.search(str(row.get("Reclaimable", "")))
        if match:
            unit = _DECIMAL_UNITS[match.group(2).upper()]
            sizes[_DOCKER[row["Type"]][0]] = int(float(match.group(1)) * unit)
    return sizes


def _docker_items() -> list[Item]:
    sizes = _docker_reclaimable() or {}
    return [
        Item(item_id, title, tier, sizes[item_id], note, command=command)
        for item_id, tier, title, command, note in _DOCKER.values()
        if item_id in sizes
    ]


def _journal_excess() -> int | None:
    """Return how much the journal holds beyond what is kept, if it can be read."""
    if shutil.which("journalctl") is None:
        return None
    match = _JOURNAL_SIZE.search(_capture(["journalctl", "--disk-usage"]) or "")
    if match is None:
        return None
    usage = int(float(match.group(1)) * _BINARY_UNITS[match.group(2)])
    return max(0, usage - JOURNAL_KEEP_BYTES)


def _journal_item() -> list[Item]:
    excess = _journal_excess()
    if excess is None:
        return []
    kept = JOURNAL_KEEP_BYTES // 1024**2
    return [
        Item(
            "journal",
            f"systemd journal beyond the newest {kept} MiB",
            "root",
            excess,
            "Older system logs are gone for good.",
            command=("journalctl", f"--vacuum-size={kept}M"),
        )
    ]


def _recycle_bin_item() -> list[Item]:
    size = _recycle_bin_size()
    return [Item("trash", "Recycle Bin", "review", size, TRASH_NOTE)] if size > 0 else []


def _measured(item: Item) -> Item:
    return replace(item, size_bytes=sum(directory_size(path) for path in item.paths))


def _directory(environ: Mapping[str, str], name: str, default: Path) -> Path:
    """Return the directory an environment variable names, if it is absolute."""
    value = environ.get(name, "")
    return Path(value) if value and os.path.isabs(value) else default


def _existing(*paths: Path) -> tuple[Path, ...]:
    return tuple(path for path in paths if path.is_dir())


class _Drafts:
    """Directory-backed items being listed, sizes not yet measured."""

    def __init__(self) -> None:
        self.items: list[Item] = []

    def add(
        self, item_id: str, title: str, tier: str, note: str, paths: tuple[Path, ...], *cleaner: str
    ) -> None:
        """Add an item when its directories exist.

        ``cleaner`` is the tool's own command for the job; it is used when the
        tool is installed, and the directories are emptied directly otherwise.
        """
        if not paths:
            return
        command = cleaner if cleaner and shutil.which(cleaner[0]) else ()
        self.items.append(Item(item_id, title, tier, 0, note, paths=paths, command=command))

    def add_other_caches(self, cache: Path) -> None:
        """Offer whatever else sits in the cache directory, for review."""
        covered = {os.path.realpath(path) for item in self.items for path in item.paths}
        try:
            with os.scandir(cache) as entries:
                others = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            others = []
        for entry in others:
            # A link may lead anywhere, so it is never treated as a cache.
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if os.path.realpath(entry.path) in covered:
                continue
            self.items.append(
                Item(
                    f"{_OTHER_PREFIX}{entry.name}",
                    f"{entry.name} cache",
                    "review",
                    0,
                    "An application's cache; it is rebuilt or downloaded again.",
                    paths=(Path(entry.path),),
                )
            )


def _path_items(home: Path, environ: Mapping[str, str], with_root: bool) -> list[Item]:
    """List every directory-backed item that exists on Linux, sizes not yet measured."""
    cache = _directory(environ, "XDG_CACHE_HOME", home / ".cache")
    data = _directory(environ, "XDG_DATA_HOME", home / ".local" / "share")
    drafts = _Drafts()
    add = drafts.add

    add(
        "uv",
        "uv cache",
        "safe",
        BUILT_AGAIN,
        _existing(_directory(environ, "UV_CACHE_DIR", cache / "uv")),
        *("uv", "cache", "clean"),
    )
    add(
        "pip",
        "pip cache",
        "safe",
        BUILT_AGAIN,
        _existing(_directory(environ, "PIP_CACHE_DIR", cache / "pip")),
    )
    add(
        "npm",
        "npm cache",
        "safe",
        DOWNLOADED_AGAIN,
        _existing(_directory(environ, "npm_config_cache", home / ".npm") / "_cacache"),
        *("npm", "cache", "clean", "--force"),
    )
    registry = _directory(environ, "CARGO_HOME", home / ".cargo") / "registry"
    add(
        "cargo",
        "Cargo registry downloads",
        "safe",
        "Crates are downloaded again when needed.",
        _existing(registry / "cache", registry / "src"),
    )
    for item_id, (title, names, note) in _CACHE_DIRECTORIES.items():
        if item_id in _CACHE_VARIABLES:
            paths = (_directory(environ, _CACHE_VARIABLES[item_id], cache / names[0]),)
        else:
            paths = tuple(cache / name for name in names)
        add(item_id, title, "safe", note, _existing(*paths))

    trash = data / "Trash"
    add(
        "trash",
        "Trash",
        "review",
        TRASH_NOTE,
        _existing(trash / "files", trash / "info", trash / "expunged"),
    )

    # The package managers' caches belong to root, so the directories are
    # only measured here; removing them is always the tool's job.
    if with_root and shutil.which("dnf"):
        add(
            "dnf",
            "dnf package cache",
            "root",
            "Package lists and downloads are fetched again.",
            _existing(*DNF_CACHE_PATHS),
            *("dnf", "clean", "all"),
        )
    if with_root and shutil.which("apt-get"):
        add(
            "apt",
            "apt downloaded packages",
            "root",
            "Packages are downloaded again when reinstalled.",
            _existing(*APT_CACHE_PATHS),
            *("apt-get", "clean"),
        )

    drafts.add_other_caches(cache)
    return drafts.items


def _windows_path_items(home: Path, environ: Mapping[str, str], with_root: bool) -> list[Item]:
    """List every directory-backed item that exists on Windows, sizes not yet measured."""
    local = _directory(environ, "LOCALAPPDATA", home / "AppData" / "Local")
    temp = _directory(environ, "TEMP", _directory(environ, "TMP", local / "Temp"))
    system = _directory(environ, "SystemRoot", Path("C:\\Windows"))
    cache = _directory(environ, "XDG_CACHE_HOME", home / ".cache")
    drafts = _Drafts()
    add = drafts.add

    add(
        "uv",
        "uv cache",
        "safe",
        BUILT_AGAIN,
        _existing(_directory(environ, "UV_CACHE_DIR", local / "uv" / "cache")),
        *("uv", "cache", "clean"),
    )
    add(
        "pip",
        "pip cache",
        "safe",
        BUILT_AGAIN,
        _existing(_directory(environ, "PIP_CACHE_DIR", local / "pip" / "Cache")),
    )
    add(
        "npm",
        "npm cache",
        "safe",
        DOWNLOADED_AGAIN,
        _existing(_directory(environ, "npm_config_cache", local / "npm-cache") / "_cacache"),
        *("npm", "cache", "clean", "--force"),
    )
    defaults = {
        "yarn": local / "Yarn" / "Cache",
        "pnpm": local / "pnpm-cache",
        "go-build": local / "go-build",
        "node-gyp": local / "node-gyp" / "Cache",
    }
    for item_id, default in defaults.items():
        title, _, note = _CACHE_DIRECTORIES[item_id]
        variable = _CACHE_VARIABLES.get(item_id)
        path = _directory(environ, variable, default) if variable else default
        add(item_id, title, "safe", note, _existing(path))
    registry = _directory(environ, "CARGO_HOME", home / ".cargo") / "registry"
    add(
        "cargo",
        "Cargo registry downloads",
        "safe",
        "Crates are downloaded again when needed.",
        _existing(registry / "cache", registry / "src"),
    )
    add(
        "gpu-shaders",
        "Graphics driver shader caches",
        "safe",
        "Games and applications compile their shaders again.",
        _existing(*(local.joinpath(*parts) for parts in _WINDOWS_SHADER_CACHES)),
    )

    add(
        "temp",
        "Temp files",
        "review",
        "Programs' leftover temporary files; files still in use are kept.",
        _existing(temp, local / "SquirrelTemp"),
    )
    add(
        "crash-dumps",
        "Crash dumps",
        "review",
        "Reports of past crashes are gone; nothing needs them to run.",
        _existing(local / "CrashDumps"),
    )

    if with_root:
        add(
            "windows-temp",
            "Windows temp files",
            "root",
            "The system's leftover temporary files; files still in use are kept.",
            _existing(system / "Temp"),
        )
        add(
            "windows-update",
            "Windows Update downloads",
            "root",
            "Updates not yet installed are downloaded again.",
            _existing(system / "SoftwareDistribution" / "Download"),
        )

    drafts.add_other_caches(cache)
    return drafts.items


def scan(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Item]:
    """List what could be removed: by tier, largest first within a tier.

    ``on_progress`` is called with how many places have been checked and how
    many there are, as each one finishes.
    """
    home = Path.home() if home is None else Path(home)
    environ = os.environ if environ is None else environ
    windows = _windows()
    if windows:
        with_root = _is_elevated() or (_sudo_enabled() and shutil.which("sudo") is not None)
    else:
        with_root = _is_root() or shutil.which("sudo") is not None

    # Sizing a big cache means reading millions of directory entries, so the
    # places are checked side by side.
    items: list[Item] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(_docker_items)]
        if windows:
            jobs.append(pool.submit(_recycle_bin_item))
            drafts = _windows_path_items(home, environ, with_root)
        else:
            if with_root:
                jobs.append(pool.submit(_journal_item))
            drafts = _path_items(home, environ, with_root)
        for draft in drafts:
            jobs.append(pool.submit(lambda draft=draft: [_measured(draft)]))
        for done, job in enumerate(as_completed(jobs), start=1):
            items += job.result()
            if on_progress is not None:
                on_progress(done, len(jobs))

    def worth_listing(item: Item) -> bool:
        other = item.id.startswith(_OTHER_PREFIX)
        return item.size_bytes >= (MINIMUM_OTHER_BYTES if other else MINIMUM_BYTES)

    return sorted(
        filter(worth_listing, items),
        key=lambda item: (TIERS.index(item.tier), -item.size_bytes, item.id),
    )


def _remaining(item: Item) -> int:
    """Measure again what a command-backed ``item`` would give back."""
    if item.paths:
        return sum(directory_size(path) for path in item.paths)
    if item.id == "journal":
        return _journal_excess() or 0
    if item.id.startswith("docker-"):
        return (_docker_reclaimable() or {}).get(item.id, 0)
    return 0


def _clean_paths(item: Item) -> Outcome:
    """Empty an item's directories, skipping any that must not be emptied."""
    home = Path.home()
    errors = [f"{path}: {reason}" for path in item.paths if (reason := _refusal(path, home))]
    accepted = [path for path in item.paths if not _refusal(path, home)]
    # A refused directory is never walked either: it may be the whole disk.
    # Without it the size from the scan no longer applies, so measure again.
    before = sum(map(directory_size, accepted)) if errors else item.size_bytes
    in_use = 0
    for path in accepted:
        failed, kept = _empty(path, home)
        errors += failed
        in_use += kept
    freed = max(0, before - sum(map(directory_size, accepted)))
    if errors:
        more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
        return Outcome(item, False, freed, errors[0] + more, in_use=in_use)
    return Outcome(item, True, freed, in_use=in_use)


def _clean_recycle_bin(item: Item) -> Outcome:
    try:
        _empty_recycle_bin()
    except OSError as exc:
        return Outcome(item, False, 0, str(exc))
    return Outcome(item, True, max(0, item.size_bytes - _recycle_bin_size()))


def _clean_as_administrator(item: Item, ask_password: bool) -> Outcome:
    """Empty a system directory: directly when elevated, otherwise through sudo.

    Through sudo, jzr runs itself elevated for this one item, then measures
    the directories again, since the elevated run reports to its own window.
    """
    if _is_elevated():
        return _clean_paths(item)
    if not ask_password:
        # Nobody is there to answer the elevation prompt.
        return Outcome(item, False, 0, "needs an elevated terminal; run jzr from one")
    sudo = shutil.which("sudo")
    if sudo is None:
        return Outcome(item, False, 0, "needs an elevated terminal, and sudo is not on PATH")
    command = [sudo, sys.executable, "-m", "jzr_tools", "host", "clean", "--yes", "--only", item.id]
    code, message = _execute(command)
    if code != 0:
        return Outcome(item, False, 0, message or f"exit code {code}")
    left = sum(directory_size(path) for path in item.paths)
    return Outcome(item, True, max(0, item.size_bytes - left))


def clean(item: Item, *, ask_password: bool = True) -> Outcome:
    """Remove one item and report how much that gave back."""
    if _windows() and not item.command:
        if item.id == "trash" and not item.paths:
            return _clean_recycle_bin(item)
        if item.tier == "root":
            return _clean_as_administrator(item, ask_password)
    if not item.command:
        return _clean_paths(item)

    command = _resolved(item.command)
    if item.tier == "root" and not _is_root() and not _windows():
        # Without a terminal to ask on, sudo must fail instead of waiting.
        command = ["sudo", *([] if ask_password else ["-n"]), *command]
    code, message = _execute(command)
    if code != 0:
        return Outcome(item, False, 0, message or f"exit code {code}")
    return Outcome(item, True, max(0, item.size_bytes - _remaining(item)))
