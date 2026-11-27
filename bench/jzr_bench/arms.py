"""The three setups ("arms") a task runs in, each in a fresh sandbox folder."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ARMS = ("baseline", "skill", "mcp", "split")
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"

OAUTH_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_VAR = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class JzrSource:
    jzr_bin: Path
    skill_dir: Path
    mcp_command: list[str]
    split_dir: Path | None = None  # one folder per split skill, from split_skill.write_split


@dataclass(frozen=True)
class Auth:
    var: str
    value: str


@dataclass(frozen=True)
class Sandbox:
    root: Path
    home: Path
    config_dir: Path
    work: Path
    tmp: Path
    bin: Path
    mcp_config: Path

    @classmethod
    def at(cls, root: Path) -> Sandbox:
        home = root / "home"
        return cls(
            root=root,
            home=home,
            config_dir=home / ".claude",
            work=root / "work",
            tmp=root / "tmp",
            bin=root / "bin",
            mcp_config=root / "mcp.json",
        )


@dataclass(frozen=True)
class ArmSetup:
    arm: str
    sandbox: Sandbox
    env: dict[str, str]
    claude_args: list[str]


def pick_auth(environ: Mapping[str, str]) -> Auth | None:
    """The OAuth token when set, else the API key, else None."""
    for var in (OAUTH_VAR, API_KEY_VAR):
        value = environ.get(var)
        if value:
            return Auth(var=var, value=value)
    return None


def find_jzr_on(path: str) -> Path | None:
    """The first executable file named jzr in the folders of a PATH string."""
    for folder in path.split(os.pathsep):
        if not folder:
            continue
        candidate = Path(folder) / "jzr"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def prepare_arm(
    arm: str,
    root: Path,
    source: JzrSource,
    auth: Auth,
    *,
    model: str,
    max_turns: int,
    system_path: str = SYSTEM_PATH,
) -> ArmSetup:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; arms are {', '.join(ARMS)}")
    if arm == "split" and source.split_dir is None:
        raise ValueError("the split arm needs JzrSource.split_dir (the folder of the split skills)")
    found = find_jzr_on(system_path)
    if found is not None:
        raise RuntimeError(f"{found} is on the system PATH, so the baseline would have jzr; remove it first")

    sandbox = Sandbox.at(root)
    for folder in (sandbox.home, sandbox.config_dir, sandbox.work, sandbox.tmp):
        folder.mkdir(parents=True, exist_ok=True)

    path = system_path
    claude_args = [
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--permission-mode", "bypassPermissions",
        "--strict-mcp-config",
        "--model", model,
        "--max-turns", str(max_turns),
    ]  # fmt: skip

    if arm in ("skill", "split"):
        if arm == "skill":
            shutil.copytree(source.skill_dir, sandbox.config_dir / "skills" / "jzr")
        else:
            assert source.split_dir is not None
            for folder in sorted(source.split_dir.iterdir()):
                if folder.is_dir():
                    shutil.copytree(folder, sandbox.config_dir / "skills" / folder.name)
        sandbox.bin.mkdir()
        shim = sandbox.bin / "jzr"
        shim.write_text(f'#!/bin/sh\nexec {_shell_quote(str(source.jzr_bin))} "$@"\n')
        shim.chmod(0o755)
        path = f"{sandbox.bin}{os.pathsep}{system_path}"
    elif arm == "mcp":
        config = {"mcpServers": {"jzr": {"command": source.mcp_command[0], "args": list(source.mcp_command[1:])}}}
        sandbox.mcp_config.write_text(json.dumps(config, indent=2) + "\n")
        claude_args += ["--mcp-config", str(sandbox.mcp_config)]

    env = {
        "HOME": str(sandbox.home),
        "CLAUDE_CONFIG_DIR": str(sandbox.config_dir),
        "TMPDIR": str(sandbox.tmp),
        "PATH": path,
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        auth.var: auth.value,
    }
    return ArmSetup(arm=arm, sandbox=sandbox, env=env, claude_args=claude_args)
