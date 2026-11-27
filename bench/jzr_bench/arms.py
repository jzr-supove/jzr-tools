"""The setups ("arms") a task runs in, each in a fresh sandbox folder."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping, Sequence
from fnmatch import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

ARMS = ("baseline", "skill", "mcp")
# The skills `jzr agent install` sets up; a test keeps this equal to jzr_tools.core.skill.SKILL_NAMES.
SKILLS = frozenset({"jzr-media", "jzr-pdf", "jzr-data", "jzr-system"})
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"

MACHINES = ("full", "bare")
# What the bare machine does not have: media, PDF and office programs, and single-purpose converters.
# General tools (python3, pip, curl, git, jq, rename, the shell tools) stay.
BARE_HIDDEN_PROGRAMS = (
    # ffmpeg and other players and encoders
    "ffmpeg", "ffprobe", "ffplay", "avconv", "avprobe", "mencoder", "mplayer", "mpv", "vlc", "cvlc",
    "gst-launch-1.0", "HandBrakeCLI", "mkvmerge", "mkvextract", "mediainfo", "MP4Box", "atomicparsley",
    # ImageMagick and GraphicsMagick
    "magick", "convert", "identify", "mogrify", "composite", "montage", "compare", "stream", "display", "import",
    "animate", "conjure", "gm",
    # other picture tools
    "vips", "vipsthumbnail", "heif-convert", "heif-enc", "heif-dec", "heif-info", "heif-thumbnailer",
    "cwebp", "dwebp", "gif2webp", "img2webp", "webpmux", "gifsicle", "pngquant", "optipng", "pngcrush",
    "jpegoptim", "jpegtran", "cjpeg", "djpeg", "exiftool", "exiv2", "jhead", "rsvg-convert", "inkscape",
    "gimp", "krita", "darktable-cli", "dcraw", "avifenc", "avifdec", "cjxl", "djxl", "img2pdf",
    # PDF
    "gs", "ghostscript", "qpdf", "pdftk", "pdfunite", "pdfseparate", "pdftoppm", "pdftocairo", "pdftotext",
    "pdfinfo", "pdfimages", "pdfdetach", "pdfattach", "pdffonts", "pdftohtml", "pdftops", "pdfsig",
    "mutool", "mudraw", "ocrmypdf", "tesseract", "ps2pdf", "pdf2ps", "cpdf",
    # office and documents
    "soffice", "libreoffice", "localc", "lowriter", "unoconv", "pandoc", "xlsx2csv", "in2csv", "csvformat",
    "ssconvert", "gnumeric", "catdoc", "xls2csv", "antiword", "docx2txt",
    # audio
    "sox", "soxi", "lame", "flac", "metaflac", "oggenc", "opusenc", "mid3v2", "mid3cp", "mid3iconv",
    "mutagen-inspect", "mutagen-pony", "moggsplit", "id3v2", "id3tool", "eyeD3", "kid3-cli",
)  # fmt: skip

OAUTH_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_VAR = "ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class JzrSource:
    jzr_bin: Path
    skills_dir: Path  # holds one folder for each of SKILLS
    mcp_command: list[str]


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
    wrapper: list[str] = field(default_factory=list)  # goes in front of the claude command; [] for none
    machine: str = "full"


@dataclass(frozen=True)
class BareMachine:
    """What to hide for a run on the bare machine, and jzr's own ffmpeg for the jzr arms."""

    bin_dirs: tuple[Path, ...]  # the real folders of the system PATH, each once
    site_dirs: tuple[Path, ...]  # the system python3's site-packages folders that exist
    jzr_ffmpeg: Path | None = None  # a folder laid out as <jzr data dir>/ffmpeg


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


def private_tmp_wrapper(bwrap: str, sandbox: Sandbox, extra: Sequence[str] = ()) -> list[str]:
    """The bubblewrap command that gives its child the sandbox's tmp folder as /tmp.

    Everything else is the machine's own file system, unless `extra` (more bubblewrap arguments,
    such as `bare_args`) says otherwise. A sandbox that itself lies in /tmp is mounted again at its
    own path, or the new /tmp would hide the working folder and HOME.
    """
    wrapper = [bwrap, "--dev-bind", "/", "/", "--bind", str(sandbox.tmp), "/tmp"]
    if sandbox.root.resolve().is_relative_to(Path("/tmp").resolve()):
        wrapper += ["--bind", str(sandbox.root), str(sandbox.root)]
    return [*wrapper, *extra, "--die-with-parent", "--chdir", str(sandbox.work), "--"]


def find_bare_machine(system_path: str = SYSTEM_PATH) -> BareMachine:
    """The folders to hide things in: the PATH's real folders and the system python3's site-packages."""
    bin_dirs: list[Path] = []
    python: Path | None = None
    for folder in system_path.split(os.pathsep):
        if not folder:
            continue
        real = Path(os.path.realpath(folder))
        if not real.is_dir():
            continue
        if real not in bin_dirs:
            bin_dirs.append(real)
        candidate = Path(folder) / "python3"
        if python is None and candidate.is_file() and os.access(candidate, os.X_OK):
            python = candidate

    site_dirs: list[Path] = []
    if python is not None:
        code = "import json, site; print(json.dumps(site.getsitepackages()))"
        try:
            done = subprocess.run(
                [str(python), "-c", code], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
            )
            listed = json.loads(done.stdout) if done.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, ValueError):
            listed = []
        for name in listed if isinstance(listed, list) else []:
            path = Path(str(name))
            if path.is_dir() and path not in site_dirs:
                site_dirs.append(path)
    return BareMachine(bin_dirs=tuple(bin_dirs), site_dirs=tuple(site_dirs))


def bare_args(bare: BareMachine, sandbox: Sandbox, *, jzr: bool) -> list[str]:
    """The bubblewrap arguments that turn the machine into the bare one for this sandbox.

    Programs: an overlay over each PATH folder whose upper layer holds a whiteout (a 0/0 character
    device, which any user may make) for every hidden program, so the names do not exist at all.
    Python: an empty folder over each site-packages folder, with pip put back. With `jzr`, jzr's own
    ffmpeg appears where jzr looks for it in the sandbox's HOME.
    """
    args: list[str] = []
    for number, folder in enumerate(bare.bin_dirs):
        upper = sandbox.root / "overlay" / str(number) / "upper"
        work = sandbox.root / "overlay" / str(number) / "work"
        upper.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        for name in BARE_HIDDEN_PROGRAMS:
            if not os.path.lexists(upper / name):
                os.mknod(upper / name, stat.S_IFCHR, os.makedev(0, 0))
        args += ["--overlay-src", str(folder), "--overlay", str(upper), str(work), str(folder)]
    for site in bare.site_dirs:
        args += ["--tmpfs", str(site)]
        for entry in sorted(site.iterdir()):
            if entry.name == "pip" or fnmatch(entry.name, "pip-*.dist-info"):
                args += ["--ro-bind", str(entry), str(entry)]
    if jzr and bare.jzr_ffmpeg is not None:
        args += ["--ro-bind", str(bare.jzr_ffmpeg), str(sandbox.home / ".local" / "share" / "jzr" / "ffmpeg")]
    return args


def remove_sandbox(root: Path) -> None:
    """Delete a sandbox folder. An overlay leaves a work folder nobody may read, which is opened up first."""
    overlay = root / "overlay"
    if overlay.is_dir():
        for folder, names, _ in os.walk(overlay):
            for name in names:
                try:
                    os.chmod(Path(folder) / name, 0o700)
                except OSError:
                    pass
    shutil.rmtree(root, ignore_errors=True)


def prepare_arm(
    arm: str,
    root: Path,
    source: JzrSource,
    auth: Auth,
    *,
    model: str,
    max_turns: int,
    system_path: str = SYSTEM_PATH,
    bwrap: str | None = None,
    bare: BareMachine | None = None,
) -> ArmSetup:
    """Make the sandbox for one run.

    With `bwrap` the run gets a private /tmp and no TMPDIR; with `bare` as well, the bare machine.
    """
    if bare is not None and bwrap is None:
        raise ValueError("the bare machine needs bwrap: it is made inside the run's own mount namespace")
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; arms are {', '.join(ARMS)}")
    if arm == "skill":
        missing = sorted(name for name in SKILLS if not (source.skills_dir / name).is_dir())
        if missing:
            raise ValueError(f"{source.skills_dir} has no skill folder for {', '.join(missing)}")
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

    if arm == "skill":
        for name in sorted(SKILLS):
            shutil.copytree(source.skills_dir / name, sandbox.config_dir / "skills" / name)
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
        "PATH": path,
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        auth.var: auth.value,
    }
    if bwrap is None:
        env["TMPDIR"] = str(sandbox.tmp)
        return ArmSetup(arm=arm, sandbox=sandbox, env=env, claude_args=claude_args)
    if bare is None:
        wrapper = private_tmp_wrapper(bwrap, sandbox)
        return ArmSetup(arm=arm, sandbox=sandbox, env=env, claude_args=claude_args, wrapper=wrapper)
    wrapper = private_tmp_wrapper(bwrap, sandbox, bare_args(bare, sandbox, jzr=arm in ("skill", "mcp")))
    return ArmSetup(arm=arm, sandbox=sandbox, env=env, claude_args=claude_args, wrapper=wrapper, machine="bare")
