"""Build the Claude Code plugin and marketplace files from the packaged skill.

The files are committed at the root of the repository, so that
``claude plugin marketplace add jzr-supove/jzr-tools`` finds them; a test
fails when they no longer match the skill or the version.
``python -m jzr_tools.commands._skill_text`` writes them together with the
skill.
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
SKILL_FOLDER = f"plugins/{PLUGIN}/skills/jzr/"


def _json(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def server(version: str) -> dict[str, object]:
    """The MCP server entry: the released package of this version, run with uvx."""
    return {
        "command": "uvx",
        "args": ["--from", f"jzr-tools[mcp]=={version}", "jzr", "agent", "mcp"],
    }


def plugin_files(skill: dict[str, str], version: str) -> dict[str, bytes]:
    """Every plugin file by its path from the repository root; ``skill`` is ``_skill_text.build()``."""
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
    for name, text in skill.items():
        files[SKILL_FOLDER + name] = text.encode("utf-8")
    return files


def write(root: Path, files: dict[str, bytes]) -> list[Path]:
    """Bring the files under ``root`` up to date; the ones that changed. Others are left alone."""
    changed = []
    for name, content in files.items():
        path = root / name
        if path.is_file() and path.read_bytes().replace(b"\r\n", b"\n") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        changed.append(path)
    return changed


def repository_root(package_dir: Path) -> Path | None:
    """The checkout that holds ``src/jzr_tools``, when there is one (it has a pyproject.toml)."""
    root = package_dir.parent.parent
    if package_dir.parent.name == "src" and (root / "pyproject.toml").is_file():
        return root
    return None
