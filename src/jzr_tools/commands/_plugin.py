"""Build the Claude Code plugin and marketplace files from the packaged skills.

The files are committed at the root of the repository, so that
``claude plugin marketplace add jzr-supove/jzr-tools`` finds them; a test
fails when they no longer match the skills or the version.
``python -m jzr_tools.commands._skill_text`` writes them together with the
skills.
"""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN = "jzr"
MARKETPLACE_NAME = "jzr-tools"
OWNER = {"name": "JZR SUPOVE"}
REPOSITORY = "https://github.com/jzr-supove/jzr-tools"
DESCRIPTION = (
    "Everyday file and machine jobs from the jzr command line: convert, compress and "
    "inspect images, video, audio and PDFs, convert data, free ports and more."
)
KEYWORDS = ["cli", "images", "video", "audio", "pdf", "files"]
MARKETPLACE_DESCRIPTION = "The jzr plugin: a skill and an MCP server for the jzr command line."

MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
PLUGIN_FILE = f"plugins/{PLUGIN}/.claude-plugin/plugin.json"
# Holds one folder for each skill; a file in it that is not built is deleted.
SKILLS_FOLDER = f"plugins/{PLUGIN}/skills/"


def _json(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def server(version: str) -> dict[str, object]:
    """The MCP server entry: the released package of this version, run with uvx."""
    return {
        "command": "uvx",
        "args": ["--from", f"jzr-tools[mcp]=={version}", "jzr", "agent", "mcp"],
    }


def plugin_files(skills: dict[str, dict[str, str]], version: str) -> dict[str, bytes]:
    """Every plugin file by its path from the repository root; ``skills`` is ``_skill_text.build()``."""
    manifest = {
        "name": PLUGIN,
        "version": version,
        "description": DESCRIPTION,
        "author": OWNER,
        "homepage": REPOSITORY,
        "repository": REPOSITORY,
        "license": "Apache-2.0",
        "keywords": KEYWORDS,
        "mcpServers": {"jzr": server(version)},
    }
    marketplace = {
        "name": MARKETPLACE_NAME,
        "owner": OWNER,
        "description": MARKETPLACE_DESCRIPTION,
        "plugins": [
            {
                "name": PLUGIN,
                "source": f"./plugins/{PLUGIN}",
                "description": DESCRIPTION,
                "version": version,
            }
        ],
    }
    files = {MARKETPLACE_FILE: _json(marketplace), PLUGIN_FILE: _json(manifest)}
    for skill, texts in skills.items():
        for name, text in texts.items():
            files[f"{SKILLS_FOLDER}{skill}/{name}"] = text.encode("utf-8")
    return files


def write(root: Path, files: dict[str, bytes]) -> list[Path]:
    """Bring the files under ``root`` up to date; the ones that changed or were deleted.

    A file under the skills folder that is not in ``files`` is deleted; files
    anywhere else are left alone.
    """
    return sync(root, files, root / SKILLS_FOLDER)


def sync(root: Path, files: dict[str, bytes], owned: Path) -> list[Path]:
    """Write ``files`` (paths from ``root``) and delete every other file inside ``owned``.

    A file whose only difference is CRLF line endings counts as current.
    Returns the files written, then the ones deleted.
    """
    changed = []
    for name, content in files.items():
        path = root / name
        if path.is_file() and path.read_bytes().replace(b"\r\n", b"\n") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        changed.append(path)
    if not owned.is_dir():
        return changed
    for path in sorted(owned.rglob("*")):
        if path.is_file() and path.relative_to(root).as_posix() not in files:
            path.unlink()
            changed.append(path)
    # Deepest first, so a folder emptied by the loop above goes too.
    for folder in sorted((p for p in owned.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return changed


def repository_root(package_dir: Path) -> Path | None:
    """The checkout that holds ``src/jzr_tools``, when there is one (it has a pyproject.toml)."""
    root = package_dir.parent.parent
    if package_dir.parent.name == "src" and (root / "pyproject.toml").is_file():
        return root
    return None
