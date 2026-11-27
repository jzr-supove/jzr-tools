"""The jzr skill cut into four domain skills, for the benchmark's `split` arm only.

Each skill is the built SKILL.md with its own front matter and only its domain's part of the command list;
the intro, Rules and the other sections stay whole. Nothing here is installed by jzr.
"""

from __future__ import annotations

import re
from pathlib import Path

START = "<!-- commands:start -->"
END = "<!-- commands:end -->"

DOMAINS: dict[str, tuple[str, ...]] = {
    "jzr-media": ("image", "video", "audio", "ffmpeg"),
    "jzr-pdf": ("pdf",),
    "jzr-data": ("data", "text", "tools", "random", "env", "file"),
    "jzr-system": ("port", "host", "bind", "completion"),
}

DESCRIPTIONS: dict[str, str] = {
    "jzr-media": (
        "Run the jzr command line for image, video and audio files. Convert, compress, resize, trim or inspect "
        "images, video and audio; make GIFs; edit audio tags. Use it instead of ffmpeg or Pillow scripts."
    ),
    "jzr-pdf": (
        "Run the jzr command line for PDF files. Merge, split, reorder, rotate or remove pages; pull out text "
        "or images; show page counts and sizes. Use it instead of pypdf scripts or Ghostscript."
    ),
    "jzr-data": (
        "Run the jzr command line for data, text and file jobs. Convert JSON, YAML, CSV, TOML and Excel data; "
        "rename files; hash files; encode, diff or slug text; read JWTs; convert timestamps; make UUIDs or "
        "passwords."
    ),
    "jzr-system": (
        "Run the jzr command line for machine jobs. Find or free a port, see what holds it, clean caches, "
        "and set up shell completion."
    ),
}

SPLIT_SKILLS = frozenset(DOMAINS)

_HEADING = re.compile(r"^### (\S+)", re.M)


def _sections(block: str) -> list[tuple[str, str]]:
    """The command list as (stem, its heading and lines) in order."""
    starts = [match.start() for match in _HEADING.finditer(block)]
    parts = [block[a:b] for a, b in zip(starts, starts[1:] + [len(block)], strict=True)]
    return [(_HEADING.match(part).group(1), part) for part in parts]  # type: ignore[union-attr]


def build_split(built: dict[str, str]) -> dict[str, dict[str, str]]:
    """From `_skill_text.build()` (path -> text) to skill name -> {path in that skill -> text}."""
    skill = built["SKILL.md"]
    if not skill.startswith("---\n"):
        raise ValueError("the built SKILL.md has no front matter")
    end_of_front = skill.index("\n---\n", 4) + len("\n---\n")
    body = skill[end_of_front:]
    before, _, rest = body.partition(START + "\n")
    block, marker, after = rest.partition(END)
    if not marker:
        raise ValueError("the built SKILL.md has no command list markers")

    sections = _sections(block)
    owner = {stem: name for name, stems in DOMAINS.items() for stem in stems}
    unknown = [stem for stem, _ in sections if stem not in owner]
    if unknown:
        raise ValueError(f"stems in no split domain: {', '.join(unknown)}")

    result: dict[str, dict[str, str]] = {}
    for name, stems in DOMAINS.items():
        front = f"---\nname: {name}\ndescription: {DESCRIPTIONS[name]}\n---\n"
        own = "".join(text for stem, text in sections if owner[stem] == name)
        files = {"SKILL.md": front + before + START + "\n" + own + END + after}
        for stem in stems:
            path = f"reference/{stem}.md"
            if path in built:
                files[path] = built[path]
        result[name] = files
    return result


def write_split(target: Path) -> list[str]:
    """Write the split of the current built skill as target/<skill>/...; returns the skill names."""
    from jzr_tools.commands._skill_text import build

    for name, files in build_split(build()).items():
        for path, text in files.items():
            file = target / name / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
    return list(DOMAINS)
