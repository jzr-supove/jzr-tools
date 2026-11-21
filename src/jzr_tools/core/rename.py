"""Planning and applying batch file renames.

A plan maps each file to a new name in the same folder. Names are built from
the old stem in a fixed order (replace, regex, case, prefix and suffix,
template), the extension is changed only on request, and names that clash or
that Windows cannot hold are caught before anything is renamed. Applying goes
through temporary names, so swaps, chains and case-only changes need no
special handling.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as _replace
from datetime import date
import os
from pathlib import Path
import re
import secrets
import string
import sys
from typing import Callable, Sequence

from jzr_tools.core import text

CASES = ("lower", "upper", "title", "slug", "snake")
TEMPLATE_FIELDS = ("name", "n", "parent", "date", "ext")
SORTS = ("name", "mtime", "size")

# Accepted for --case but not listed: the same as "slug".
_CASE_ALIASES = {"kebab": "slug"}

_WINDOWS_BAD_CHARACTERS = frozenset('<>:"|?*')
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)
_MAX_NAME_LENGTH = 255
_HIDDEN_ATTRIBUTES = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
TEMP_PREFIX = "jzr-tmp-"


class RenameError(ValueError):
    """Options that cannot work; the message is meant for the user."""


@dataclass(frozen=True)
class RenameOptions:
    """How new names are made from old ones."""

    replace: tuple[tuple[str, str], ...] = ()
    regex: tuple[tuple[str, str], ...] = ()
    ignore_case: bool = False
    case: str | None = None
    prefix: str = ""
    suffix: str = ""
    template: str | None = None
    ext: str | None = None
    lower_ext: bool = False
    start: int = 1

    def changes_anything(self) -> bool:
        return bool(
            self.replace
            or self.regex
            or self.case is not None
            or self.prefix
            or self.suffix
            or self.template is not None
            or self.ext is not None
            or self.lower_ext
        )


@dataclass(frozen=True)
class RenameItem:
    """One file of a plan and what happened to it."""

    source: Path
    target: Path
    status: str
    detail: str = ""


class RenameInterrupted(Exception):
    """Ctrl+C during a rename; every file is back at its old name or at its new one."""

    def __init__(self, items: list[RenameItem]) -> None:
        super().__init__("interrupted")
        self.items = items


# --- options -----------------------------------------------------------------


def check_options(options: RenameOptions) -> None:
    """Raise RenameError for options that cannot be applied to any name."""
    for old, _new in options.replace:
        if not old:
            raise RenameError("--replace needs some text to look for.")
    for pattern, replacement in options.regex:
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise RenameError(
                f"{pattern!r} is not a valid regular expression ({error})."
            ) from error
        try:
            compiled.sub(replacement, "")
        except (re.error, IndexError) as error:
            raise RenameError(
                f"{replacement!r} is not a valid replacement for {pattern!r} ({error})."
            ) from error
    if options.case is not None and _case_name(options.case) not in CASES:
        raise RenameError(
            f"Unknown case {options.case!r}; use one of: {', '.join(CASES)}."
        )
    if options.template is not None:
        _check_template(options.template)
    if options.start < 0:
        raise RenameError(f"--start must be 0 or more, not {options.start}.")


def _case_name(case: str) -> str:
    return _CASE_ALIASES.get(case, case)


def _check_template(template: str) -> None:
    fields = ", ".join(f"{{{name}}}" for name in TEMPLATE_FIELDS)
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as error:
        raise RenameError(f"--template {template!r} cannot be read ({error}).") from error
    for _literal, field, _spec, _conversion in parsed:
        if field is not None and field not in TEMPLATE_FIELDS:
            raise RenameError(
                f"--template has an unknown field {{{field}}}; use {fields}."
            )
    try:
        template.format(name="name", n=1, parent="folder", date="2026-01-01", ext="ext")
    except (ValueError, KeyError, IndexError, AttributeError, TypeError) as error:
        raise RenameError(f"--template {template!r} cannot be used ({error}).") from error


# --- names -------------------------------------------------------------------


def split_name(name: str) -> tuple[str, str]:
    """Split ``name`` into stem and extension at the last dot.

    A leading dot (``.bashrc``) and a trailing one (``file.``) do not start
    an extension.
    """
    index = name.rfind(".")
    if 0 < index < len(name) - 1:
        return name[:index], name[index:]
    return name, ""


def new_name(
    name: str, options: RenameOptions, *, n: int, parent: str, modified: date
) -> str:
    """Return the new name for ``name``; the result is not checked."""
    stem, extension = split_name(name)

    for old, new in options.replace:
        if options.ignore_case:
            stem = re.sub(re.escape(old), lambda _match, new=new: new, stem, flags=re.IGNORECASE)
        else:
            stem = stem.replace(old, new)

    for pattern, replacement in options.regex:
        stem = re.sub(pattern, replacement, stem)

    if options.case is not None:
        stem = _apply_case(stem, _case_name(options.case))

    stem = f"{options.prefix}{stem}{options.suffix}"

    if options.template is not None:
        stem = options.template.format(
            name=stem,
            n=n,
            parent=parent,
            date=modified.isoformat(),
            ext=extension[1:],
        )

    if options.ext is not None:
        new_extension = options.ext[1:] if options.ext.startswith(".") else options.ext
        extension = f".{new_extension}" if new_extension else ""
    if options.lower_ext:
        extension = extension.lower()

    return stem + extension


def _apply_case(stem: str, case: str) -> str:
    if case == "lower":
        return stem.lower()
    if case == "upper":
        return stem.upper()
    if case == "title":
        return stem.title()
    if case == "slug":
        return text.slugify(stem)
    if case == "snake":
        return text.slugify(stem, separator="_")
    raise RenameError(f"Unknown case {case!r}; use one of: {', '.join(CASES)}.")


def name_problem(name: str) -> str | None:
    """Return why ``name`` cannot be used as a file name, or None.

    Names Windows cannot hold are refused everywhere, so a renamed folder
    still works when it is copied to a Windows machine.
    """
    if not name:
        return "the new name is empty"
    if name in (".", ".."):
        return f'new name "{name}" is not a file name'
    if "/" in name or "\\" in name:
        return f'new name "{name}" would leave the folder'
    for character in name:
        if character in _WINDOWS_BAD_CHARACTERS or ord(character) < 0x20:
            shown = character if ord(character) >= 0x20 else f"\\x{ord(character):02x}"
            return (
                f'new name "{_printable(name)}" has a character Windows does not allow ({shown})'
            )
    if name.endswith((".", " ")):
        return f'new name "{name}" ends in a dot or space, which Windows drops'
    device = name.split(".", 1)[0].upper()
    if device in _DEVICE_NAMES:
        return f'new name "{name}" is a device name on Windows ({device})'
    if len(name) > _MAX_NAME_LENGTH:
        return f"the new name is longer than {_MAX_NAME_LENGTH} characters"
    return None


def _printable(name: str) -> str:
    return "".join(
        character if ord(character) >= 0x20 else f"\\x{ord(character):02x}"
        for character in name
    )


# --- finding and ordering files ----------------------------------------------


def natural_key(name: str) -> tuple:
    """Sort key that ignores case and compares digit runs as numbers."""
    parts = re.split(r"(\d+)", name.casefold())
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts))


def order_sources(
    paths: Sequence[Path], *, sort: str | None, reverse: bool
) -> list[Path]:
    """Return ``paths`` in the order their numbers are given out."""
    if sort is None:
        ordered = list(paths)
    elif sort == "name":
        ordered = sorted(paths, key=lambda path: (natural_key(path.name), natural_key(str(path))))
    elif sort in ("mtime", "size"):
        def key(path: Path) -> tuple:
            try:
                info = path.stat()
                value = info.st_mtime if sort == "mtime" else info.st_size
            except OSError:
                value = 0
            return (value, natural_key(path.name), natural_key(str(path)))

        ordered = sorted(paths, key=key)
    else:
        raise RenameError(f"Unknown sort {sort!r}; use one of: {', '.join(SORTS)}.")
    if reverse:
        ordered.reverse()
    return ordered


def is_hidden(path: Path) -> bool:
    """Whether ``path`` is hidden: a dot name, or on Windows the hidden or system flag."""
    if path.name.startswith("."):
        return True
    if sys.platform == "win32":
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attributes & _HIDDEN_ATTRIBUTES)
    return False


def find_files(
    directory: Path,
    *,
    pattern: re.Pattern[str] | None,
    recursive: bool,
    hidden: bool,
) -> list[Path]:
    """Files under ``directory``, folder by folder, each folder in natural order.

    ``pattern`` is searched in each path relative to ``directory``. Without
    ``hidden``, hidden files are left out and hidden folders are not entered.
    """
    found: list[Path] = []

    def visit(folder: Path) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: natural_key(entry.name))
        except OSError:
            return
        subfolders: list[Path] = []
        for entry in entries:
            path = folder / entry.name
            if not hidden and is_hidden(path):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    subfolders.append(path)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            relative = path.relative_to(directory).as_posix()
            if pattern is not None and pattern.search(relative) is None:
                continue
            found.append(path)
        if recursive:
            for subfolder in subfolders:
                visit(subfolder)

    visit(directory)
    return found


# --- planning ----------------------------------------------------------------


def _folder_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path.parent)).casefold()


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def _is_blocked(target: Path, moving: set[tuple[int, int]]) -> bool:
    """Whether ``target`` belongs to a file that is not moving away."""
    if not os.path.lexists(target):
        return False
    identity = _identity(target)
    return identity is None or identity not in moving


def _block_stuck(items: list[RenameItem]) -> list[RenameItem]:
    """Skip renames onto files that stay, until no more skips follow."""
    items = list(items)
    while True:
        moving = {
            identity
            for item in items
            if item.status == "rename" and (identity := _identity(item.source)) is not None
        }
        changed = False
        for index, item in enumerate(items):
            if item.status == "rename" and _is_blocked(item.target, moving):
                items[index] = _replace(
                    item, target=item.source, status="skipped", detail=f"{item.target.name} exists"
                )
                changed = True
                break
        if not changed:
            return items


def plan_renames(sources: Sequence[Path], options: RenameOptions) -> list[RenameItem]:
    """Return one item per source, in order, saying what would happen to it."""
    items: list[RenameItem] = []
    counters: dict[str, int] = {}
    for source in sources:
        folder = _folder_key(source)
        number = counters.get(folder, options.start)
        counters[folder] = number + 1

        if not os.path.lexists(source):
            items.append(RenameItem(source, source, "failed", "not found"))
            continue
        if source.is_dir():
            items.append(
                RenameItem(source, source, "failed", f"{source.name} is a folder; use --dir {source.name}")
            )
            continue
        try:
            modified = date.fromtimestamp(source.stat().st_mtime)
        except OSError as error:
            items.append(RenameItem(source, source, "failed", _reason(error)))
            continue
        parent = source.resolve().parent.name
        name = new_name(source.name, options, n=number, parent=parent, modified=modified)
        problem = name_problem(name)
        if problem is not None:
            items.append(RenameItem(source, source, "failed", problem))
        elif name == source.name:
            items.append(RenameItem(source, source, "unchanged"))
        else:
            items.append(RenameItem(source, source.with_name(name), "rename"))

    items = _number_clashes(items)
    return _block_stuck(items)


def _number_clashes(items: list[RenameItem]) -> list[RenameItem]:
    """Give later renames that share a new name ``-2``, ``-3``, ... after the stem."""
    moving = {
        identity
        for item in items
        if item.status == "rename" and (identity := _identity(item.source)) is not None
    }
    claimed: set[tuple[str, str]] = set()
    result: list[RenameItem] = []
    for item in items:
        if item.status != "rename":
            result.append(item)
            continue
        folder = _folder_key(item.target)
        key = (folder, item.target.name.casefold())
        if key not in claimed:
            claimed.add(key)
            result.append(item)
            continue
        stem, extension = split_name(item.target.name)
        counter = 2
        while True:
            candidate = item.target.with_name(f"{stem}-{counter}{extension}")
            candidate_key = (folder, candidate.name.casefold())
            if candidate_key not in claimed and not _is_blocked(candidate, moving):
                break
            counter += 1
        claimed.add(candidate_key)
        result.append(_replace(item, target=candidate, detail=f"clash: -{counter} added"))
    return result


# --- applying ----------------------------------------------------------------


def _reason(error: OSError) -> str:
    return error.strerror or str(error)


def apply_renames(
    items: Sequence[RenameItem],
    *,
    rename: Callable[[Path, Path], None] = os.rename,
) -> list[RenameItem]:
    """Rename every ``rename`` item, through a temporary name in its folder.

    One file failing fails alone and keeps its old name. On Ctrl+C every file
    on a temporary name is put back, then RenameInterrupted is raised.
    """
    result = _block_stuck(list(items))
    pending = [index for index, item in enumerate(result) if item.status == "rename"]
    temps: dict[int, Path] = {}

    def put_back(index: int, status: str, detail: str) -> None:
        item = result[index]
        temp = temps.pop(index)
        try:
            rename(temp, item.source)
        except OSError as error:
            result[index] = _replace(
                item,
                target=temp,
                status="failed",
                detail=f"{detail or 'could not rename: ' + _reason(error)}; the file is now {temp.name}",
            )
            return
        # An interrupted file keeps its planned target, so the report can
        # still say where it was going.
        target = item.target if detail == "interrupted" else item.source
        result[index] = _replace(item, target=target, status=status, detail=detail)

    try:
        for index in pending:
            item = result[index]
            temp = item.source.with_name(
                f"{TEMP_PREFIX}{secrets.token_hex(8)}{split_name(item.source.name)[1]}"
            )
            try:
                rename(item.source, temp)
            except OSError as error:
                result[index] = _replace(
                    item,
                    target=item.source,
                    status="failed",
                    detail=f"could not rename: {_reason(error)}",
                )
                continue
            temps[index] = temp

        for index in list(temps):
            item = result[index]
            if os.path.lexists(item.target):
                put_back(index, "skipped", f"{item.target.name} exists")
                continue
            try:
                rename(temps[index], item.target)
            except OSError as error:
                put_back(index, "failed", f"could not rename: {_reason(error)}")
                continue
            del temps[index]
            result[index] = _replace(item, status="renamed")
    except KeyboardInterrupt:
        for index in list(temps):
            try:
                put_back(index, "skipped", "interrupted")
            except KeyboardInterrupt:
                continue
        for index in pending:
            if result[index].status == "rename":
                result[index] = _replace(result[index], status="skipped", detail="interrupted")
        raise RenameInterrupted(result) from None

    return result
