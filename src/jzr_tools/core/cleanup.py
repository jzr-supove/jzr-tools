"""Find and remove caches and other disposable data.

``scan`` lists what could be removed and how much each item would give back;
``clean`` removes one item. Neither decides what the user wants gone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import shutil
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

KNOWN_IDS: tuple[str, ...] = (
    "docker-build-cache",
    "uv",
    "pip",
    "npm",
    "cargo",
    *_CACHE_DIRECTORIES,
    "trash",
    "docker-containers",
    "docker-images",
    "journal",
    "dnf",
    "apt",
)
_OTHER_PREFIX = "cache:"

_DECIMAL_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
_BINARY_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_DOCKER_SIZE = re.compile(r"([\d.]+)\s*([kKMGT]?B)")
_JOURNAL_SIZE = re.compile(r"take up ([\d.]+)\s*([BKMGT])")


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
            return " ".join(self.command)
        return ", ".join(str(path) for path in self.paths)


@dataclass(frozen=True)
class Outcome:
    """What removing one item did."""

    item: Item
    ok: bool
    freed_bytes: int
    error: str = ""


def is_known_id(item_id: str) -> bool:
    """Return whether ``item_id`` names a kind of item ``scan`` can report."""
    return item_id in KNOWN_IDS or (
        item_id.startswith(_OTHER_PREFIX) and len(item_id) > len(_OTHER_PREFIX)
    )


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


def directory_size(path: Path) -> int:
    """Return the disk space used by everything inside ``path``.

    Hard-linked files count once, links are not followed, and whatever cannot
    be read is left out.
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
                if entry.is_dir(follow_symlinks=False):
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


def empty_directory(path: Path, *, home: Path | None = None) -> list[str]:
    """Remove everything inside ``path`` and keep the directory itself.

    Returns one message per thing that could not be removed. Links inside are
    removed as links; what they point at is not touched.
    """
    refusal = _refusal(path, Path.home() if home is None else home)
    if refusal:
        return [f"{path}: {refusal}"]

    errors: list[str] = []
    try:
        with os.scandir(path) as entries:
            found = list(entries)
    except OSError as exc:
        return [f"{path}: {exc.strerror or exc}"]
    for entry in found:
        try:
            if entry.is_dir(follow_symlinks=False):
                _remove_tree(entry.path, errors)
            else:
                os.unlink(entry.path)
        except OSError as exc:
            errors.append(f"{entry.path}: {exc.strerror or exc}")
    return errors


def _docker_reclaimable() -> dict[str, int] | None:
    """Return what Docker says each kind of data would give back, by item id."""
    if shutil.which("docker") is None:
        return None
    output = _capture(["docker", "system", "df", "--format", "{{json .}}"])
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


def _measured(item: Item) -> Item:
    return replace(item, size_bytes=sum(directory_size(path) for path in item.paths))


def _directory(environ: Mapping[str, str], name: str, default: Path) -> Path:
    """Return the directory an environment variable names, if it is absolute."""
    value = environ.get(name, "")
    return Path(value) if value and os.path.isabs(value) else default


def _existing(*paths: Path) -> tuple[Path, ...]:
    return tuple(path for path in paths if path.is_dir())


def _path_items(home: Path, environ: Mapping[str, str], with_root: bool) -> list[Item]:
    """List every directory-backed item that exists, sizes not yet measured."""
    cache = _directory(environ, "XDG_CACHE_HOME", home / ".cache")
    data = _directory(environ, "XDG_DATA_HOME", home / ".local" / "share")
    items: list[Item] = []

    def add(
        item_id: str, title: str, tier: str, note: str, paths: tuple[Path, ...], *cleaner: str
    ) -> None:
        """Add an item when its directories exist.

        ``cleaner`` is the tool's own command for the job; it is used when the
        tool is installed, and the directories are emptied directly otherwise.
        """
        if not paths:
            return
        command = cleaner if cleaner and shutil.which(cleaner[0]) else ()
        items.append(Item(item_id, title, tier, 0, note, paths=paths, command=command))

    downloaded_again = "Packages are downloaded again when needed."
    add(
        "uv",
        "uv cache",
        "safe",
        "Packages are downloaded and built again when needed.",
        _existing(_directory(environ, "UV_CACHE_DIR", cache / "uv")),
        *("uv", "cache", "clean"),
    )
    add(
        "pip",
        "pip cache",
        "safe",
        "Packages are downloaded and built again when needed.",
        _existing(_directory(environ, "PIP_CACHE_DIR", cache / "pip")),
    )
    add(
        "npm",
        "npm cache",
        "safe",
        downloaded_again,
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
        add(item_id, title, "safe", note, _existing(*(cache / name for name in names)))

    trash = data / "Trash"
    add(
        "trash",
        "Trash",
        "review",
        "Files you deleted can no longer be restored.",
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

    # Whatever else sits in the cache directory, offered for review.
    covered = {os.path.realpath(path) for item in items for path in item.paths}
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
        items.append(
            Item(
                f"{_OTHER_PREFIX}{entry.name}",
                f"{entry.name} cache",
                "review",
                0,
                "An application's cache; it is rebuilt or downloaded again.",
                paths=(Path(entry.path),),
            )
        )
    return items


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
    with_root = _is_root() or shutil.which("sudo") is not None

    # Sizing a big cache means reading millions of directory entries, so the
    # places are checked side by side.
    items: list[Item] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(_docker_items)]
        if with_root:
            jobs.append(pool.submit(_journal_item))
        for draft in _path_items(home, environ, with_root):
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
    for path in accepted:
        errors += empty_directory(path, home=home)
    freed = max(0, before - sum(map(directory_size, accepted)))
    if errors:
        more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
        return Outcome(item, False, freed, errors[0] + more)
    return Outcome(item, True, freed)


def clean(item: Item, *, ask_password: bool = True) -> Outcome:
    """Remove one item and report how much that gave back."""
    if not item.command:
        return _clean_paths(item)

    command = list(item.command)
    if item.tier == "root" and not _is_root():
        # Without a terminal to ask on, sudo must fail instead of waiting.
        command = ["sudo", *([] if ask_password else ["-n"]), *command]
    code, message = _execute(command)
    if code != 0:
        return Outcome(item, False, 0, message or f"exit code {code}")
    return Outcome(item, True, max(0, item.size_bytes - _remaining(item)))
