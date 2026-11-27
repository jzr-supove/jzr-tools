"""Install the packaged agent skills into the folders AI agents read.

jzr ships four small skills, one for each kind of job, so that an agent loads
only the commands a task needs. An installed copy carries a marker file with
the jzr version it came from and a digest of its files, so a copy the user
changed, or one jzr did not write, is never replaced or removed without being
asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import shutil

SKILL_NAMES = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
# The single skill that jzr installed up to 0.3.0; `install` takes it away.
OLD_SKILL_NAMES = ("jzr",)
MARKER = ".jzr-version"
AGENTS = ("claude", "codex")

_TEMP_PREFIX = "jzr-tmp-"
# st_reparse_tag of a directory junction; Path.is_junction() needs Python 3.12.
_JUNCTION_TAG = 0xA0000003


class SkillError(Exception):
    """A problem to show to the user as it is."""


@dataclass(frozen=True)
class Target:
    """A skill folder, and the agents that read it."""

    agents: tuple[str, ...]
    path: Path

    @property
    def name(self) -> str:
        """The skill's name, which is its folder's."""
        return self.path.name


@dataclass(frozen=True)
class Outcome:
    """What happened to one target.

    ``status`` is installed, updated, current, removed, absent, refused,
    foreign (an old skill jzr did not install, left alone) or failed.
    ``detail`` is the reason for refused and failed, and for updated the
    version that was replaced, when it is known.
    """

    target: Target
    status: str
    detail: str = ""


def skills_dir() -> Path:
    """The folder inside the installed package that holds one folder for each skill."""
    return Path(__file__).resolve().parent.parent / "data" / "skill"


def source_dir(name: str) -> Path:
    """The folder of one packaged skill."""
    if name not in SKILL_NAMES:
        raise SkillError(f"no skill named {name}; the skills are {', '.join(SKILL_NAMES)}")
    return skills_dir() / name


def targets(home: Path, *, agent: str | None = None, project: Path | None = None) -> list[Target]:
    """Where the skills go: one project folder, one agent, or every agent found; folder by folder."""
    return _targets(SKILL_NAMES, home, agent, project)


def old_targets(home: Path, *, agent: str | None = None, project: Path | None = None) -> list[Target]:
    """Where the single skill of earlier versions would be, in the same folders as `targets`."""
    return _targets(OLD_SKILL_NAMES, home, agent, project)


def _targets(names: tuple[str, ...], home: Path, agent: str | None, project: Path | None) -> list[Target]:
    return [Target(agents, folder / name) for agents, folder in _folders(home, agent, project) for name in names]


def _folders(home: Path, agent: str | None, project: Path | None) -> list[tuple[tuple[str, ...], Path]]:
    """Each skills folder to use, with the agents that read it."""
    if project is not None:
        return [_folder(agent or "claude", project)]
    if agent is not None:
        return [_folder(agent, home)]

    found: list[tuple[tuple[str, ...], Path]] = []
    cursor = (home / ".cursor").is_dir()
    if (home / ".claude").is_dir():
        found.append(_folder("claude", home))
    if (home / ".codex").is_dir():
        codex = _folder("codex", home)
        found.append((("codex", "cursor"), codex[1]) if cursor else codex)
    elif cursor and not found:
        # Cursor reads Claude's folder too, so it needs its own only here.
        found.append((("cursor",), _folder("codex", home)[1]))
    if not found:
        raise SkillError(
            "no AI agent found (looked for ~/.claude, ~/.codex and ~/.cursor); "
            "name one with --agent, or use --project"
        )
    return found


def _folder(agent: str, base: Path) -> tuple[tuple[str, ...], Path]:
    # Codex and Cursor read the shared .agents folder; Claude Code has its own.
    folder = ".claude" if agent == "claude" else ".agents"
    return (agent,), base / folder / "skills"


def files(folder: Path) -> dict[str, bytes]:
    """Every file in ``folder`` but the marker, with LF line endings."""
    found: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        name = path.relative_to(folder).as_posix()
        if name != MARKER and path.is_file():
            found[name] = path.read_bytes().replace(b"\r\n", b"\n")
    return found


def digest(contents: Mapping[str, bytes]) -> str:
    """One SHA-256 over all paths and contents, the same on every platform."""
    total = hashlib.sha256()
    for name in sorted(contents):
        data = contents[name].replace(b"\r\n", b"\n")
        encoded = name.encode("utf-8")
        # The lengths keep "a" + "bc" and "ab" + "c" apart.
        total.update(f"{len(encoded)}:{len(data)}:".encode("ascii"))
        total.update(encoded)
        total.update(data)
    return total.hexdigest()


def install(
    target: Target,
    version: str,
    *,
    source: Path | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> Outcome:
    """Copy the skill named by ``target`` into it, replacing an older copy jzr installed."""
    try:
        contents = files(source or source_dir(target.name))
        status, detail = _plan_install(target.path, version, contents, overwrite)
        if status in ("installed", "updated") and not dry_run:
            _put(target.path, contents, version)
    except OSError as error:
        return Outcome(target, "failed", _reason(error))
    return Outcome(target, status, detail)


def remove(target: Target, *, overwrite: bool = False, dry_run: bool = False) -> Outcome:
    """Delete the copy in ``target``, if jzr installed it."""
    try:
        status, detail = _plan_remove(target.path, overwrite)
        if status == "removed" and not dry_run:
            _delete(target.path)
    except OSError as error:
        return Outcome(target, "failed", _reason(error))
    return Outcome(target, status, detail)


def remove_old(target: Target, *, overwrite: bool = False, dry_run: bool = False) -> Outcome:
    """Delete an earlier version's skill in ``target``; one jzr did not install is "foreign" and stays."""
    try:
        if os.path.lexists(target.path) and (_not_a_folder(target.path) or _read_marker(target.path) is None):
            return Outcome(target, "foreign")
    except OSError as error:
        return Outcome(target, "failed", _reason(error))
    return remove(target, overwrite=overwrite, dry_run=dry_run)


def _plan_install(
    path: Path, version: str, contents: Mapping[str, bytes], overwrite: bool
) -> tuple[str, str]:
    if not os.path.lexists(path):
        return "installed", ""
    blocked = _not_a_folder(path)
    if blocked:
        return "refused", blocked

    marker = _read_marker(path)
    if marker is None:
        if overwrite:
            return "updated", ""
        return "refused", "not installed by jzr; use --overwrite to replace it"

    old_version, old_digest = marker
    present = files(path)
    if digest(present) != old_digest:
        if overwrite:
            return "updated", old_version
        return "refused", "changed since jzr installed it; use --overwrite to replace it"
    if old_version == version and present == contents:
        return "current", ""
    if _numbers(old_version) > _numbers(version) and not overwrite:
        return "refused", f"installed by a newer jzr ({old_version}); use --overwrite to replace it"
    return "updated", old_version


def _plan_remove(path: Path, overwrite: bool) -> tuple[str, str]:
    if not os.path.lexists(path):
        return "absent", ""
    blocked = _not_a_folder(path)
    if blocked:
        return "refused", blocked
    marker = _read_marker(path)
    if marker is None:
        return "refused", "not installed by jzr; remove it yourself"
    if digest(files(path)) != marker[1] and not overwrite:
        return "refused", "changed since jzr installed it; use --overwrite to remove it"
    return "removed", ""


def _not_a_folder(path: Path) -> str | None:
    """Why ``path`` cannot be replaced: a link is never followed to delete what is behind it."""
    info = os.lstat(path)
    if path.is_symlink() or getattr(info, "st_reparse_tag", 0) == _JUNCTION_TAG:
        return "is a link; remove it first"
    if not path.is_dir():
        return "is a file; remove it first"
    return None


def _read_marker(folder: Path) -> tuple[str, str] | None:
    """The version and digest in the marker, or None when there is no usable one."""
    try:
        lines = (folder / MARKER).read_text(encoding="utf-8").split("\n")
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
        return None
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        return None
    return lines[0].strip(), lines[1].strip()


def _numbers(version: str) -> tuple[int, ...]:
    """``0.10.0`` as numbers; a version that is not just numbers is older than any."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


def _put(path: Path, contents: Mapping[str, bytes], version: str) -> None:
    """Write the whole folder beside ``path``, then swap it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = _temp_beside(path)
    old: Path | None = None
    try:
        for name, data in contents.items():
            file = new / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        (new / MARKER).write_bytes(f"{version}\n{digest(contents)}\n".encode("utf-8"))

        if os.path.lexists(path):
            aside = _temp_beside(path)
            os.rename(path, aside)
            old = aside
        try:
            os.rename(new, path)
        except OSError:
            if old is not None:
                os.rename(old, path)
                old = None
            raise
    finally:
        shutil.rmtree(new, ignore_errors=True)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def _delete(path: Path) -> None:
    # The rename fails as a whole when a file inside is in use on Windows, so
    # the folder is never left half deleted under its own name.
    aside = _temp_beside(path)
    os.rename(path, aside)
    shutil.rmtree(aside)


def _temp_beside(path: Path) -> Path:
    return path.parent / f"{_TEMP_PREFIX}{secrets.token_hex(4)}"


def _reason(error: OSError) -> str:
    return error.strerror or str(error)
