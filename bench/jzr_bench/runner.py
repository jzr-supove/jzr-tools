"""Run tasks through Claude Code in each arm and record what every run cost."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .arms import (
    ARMS,
    BARE_HIDDEN_PROGRAMS,
    SYSTEM_PATH,
    ArmSetup,
    BareMachine,
    JzrSource,
    Sandbox,
    bare_args,
    find_bare_machine,
    find_jzr_on,
    pick_auth,
    prepare_arm,
    private_tmp_wrapper,
    remove_sandbox,
)
from .plan import PlannedRun, plan_runs
from .pricing import fit_prices
from .report import render_markdown, summarize
from .stream import check_isolation, parse_stream
from .tasks import TASKS, Task, get_task

REPO = Path(__file__).resolve().parents[2]
# jzr's own ffmpeg for the jzr arms on the bare machine, downloaded once.
JZR_FFMPEG_CACHE = REPO / "bench" / "results" / ".cache" / "jzr-data"
JZR_ARMS = ("skill", "mcp")
COPY_NOTE = "copied-from-system.txt"  # in the cache when its ffmpeg is a copy of the machine's, not a download
STDERR_TAIL = 2000
FINAL_TEXT_KEPT = 2000


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_one(
    planned: PlannedRun,
    task: Task,
    arm_setup: ArmSetup,
    *,
    claude_bin: str,
    fixture_dir: Path,
    transcript_path: Path,
    timeout_s: float,
) -> dict:
    work = arm_setup.sandbox.work
    task.setup(work, fixture_dir)
    command = [*arm_setup.wrapper, claude_bin, "-p", task.prompt, *arm_setup.claude_args]

    started_at = _now()
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with transcript_path.open("wb") as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            command,
            cwd=work,
            env=arm_setup.env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process.pid)
            process.wait()
        # Anything the agent left running in the background (a server, a sleep) goes too.
        _kill_group(process.pid)
        err.seek(0)
        stderr_tail = err.read().decode("utf-8", errors="replace")[-STDERR_TAIL:]
    wall_s = time.monotonic() - started

    with transcript_path.open(encoding="utf-8", errors="replace") as handle:
        metrics = parse_stream(handle)
    problems = check_isolation(arm_setup.arm, metrics.init)

    error: str | None = None
    passed = False
    check_details: list[str] = []
    if timed_out:
        error = f"timed out after {timeout_s:g} s"
    elif not metrics.has_result:
        error = f"no result event (exit code {exit_code})"
    elif metrics.is_error:
        error = f"agent error: {metrics.final_text[:500]}"
    else:
        try:
            result = task.check(work, fixture_dir, metrics.final_text)
            passed, check_details = bool(result.passed), list(result.details)
        except Exception as exc:  # a broken check must not lose the run's numbers
            check_details = [f"check crashed: {type(exc).__name__}: {exc}"]

    init = metrics.init or {}
    return {
        "run_id": planned.run_id,
        "round": planned.round,
        "task": planned.task,
        "arm": planned.arm,
        "machine": planned.machine,
        "started_at": started_at,
        "wall_s": round(wall_s, 2),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error": error,
        "passed": passed,
        "check_details": check_details,
        "isolation_ok": not problems,
        "isolation_problems": problems,
        "total_cost_usd": metrics.cost_usd,
        "input_tokens": metrics.input_tokens,
        "cache_creation_input_tokens": metrics.cache_creation_input_tokens,
        "cache_read_input_tokens": metrics.cache_read_input_tokens,
        "output_tokens": metrics.output_tokens,
        "num_turns": metrics.num_turns,
        "duration_ms": metrics.duration_ms,
        "model": init.get("model"),
        "claude_code_version": init.get("claude_code_version"),
        "model_usage": metrics.model_usage,
        "tools_available": len(init.get("tools") or []),
        "tool_calls": metrics.tool_calls,
        "bash_commands": metrics.bash_commands,
        "skills_invoked": metrics.skills_invoked,
        "used_jzr_cli": metrics.used_jzr_cli,
        "used_jzr_skill": metrics.used_jzr_skill,
        "used_jzr_mcp": metrics.used_jzr_mcp,
        "auth_failed": metrics.auth_failed,
        "final_text": metrics.final_text[:FINAL_TEXT_KEPT],
        "stderr_tail": stderr_tail,
        "transcript": str(transcript_path),
    }


# -- the whole run ------------------------------------------------------------------------------


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _output(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _git_commit() -> str | None:
    commit = _output(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"])
    if commit and _output(["git", "-C", str(REPO), "status", "--porcelain", "--", "src"]):
        commit += " (src has uncommitted changes)"
    return commit


def default_sandbox_parent() -> Path:
    """Where sandboxes go: /var/tmp, so that a run's private /tmp does not cover its own sandbox."""
    var_tmp = Path("/var/tmp")
    if var_tmp.is_dir() and os.access(var_tmp, os.W_OK | os.X_OK):
        return var_tmp
    return Path(tempfile.gettempdir())


def _find_bwrap() -> tuple[str | None, str]:
    """bubblewrap's path, or None and why it cannot be used."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return None, "bwrap (bubblewrap) is not on PATH"
    try:
        probe = subprocess.run(
            [bwrap, "--dev-bind", "/", "/", "true"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"{bwrap} cannot start: {error}"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        return None, f"{bwrap} cannot start" + (f": {detail[-1]}" if detail else "")
    return bwrap, ""


def ensure_jzr_ffmpeg(jzr_bin: Path, cache: Path = JZR_FFMPEG_CACHE) -> Path:
    """The folder with jzr's own ffmpeg, as `jzr ffmpeg install` lays it out; downloaded on first use.

    The download goes into `cache` (through JZR_DATA_DIR), never into the user's own jzr data folder.
    """
    target = cache / "ffmpeg"
    if (target / "bin" / "ffmpeg").is_file():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(
            [str(jzr_bin), "ffmpeg", "install", "--yes"],
            env={**os.environ, "JZR_DATA_DIR": str(cache)},
            capture_output=True,
            text=True,
            timeout=1800,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"`jzr ffmpeg install` could not run: {error}") from error
    if done.returncode != 0 or not (target / "bin" / "ffmpeg").is_file():
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise RuntimeError("`jzr ffmpeg install` left no ffmpeg" + (f": {detail[-1]}" if detail else ""))
    return target


def copy_system_ffmpeg(cache: Path = JZR_FFMPEG_CACHE, system_path: str = SYSTEM_PATH) -> Path | None:
    """Stand in for the download: the machine's own ffmpeg and ffprobe, copied to where jzr's would be.

    For a machine where `jzr ffmpeg install` cannot download. None when the machine has no ffmpeg.
    """
    found = {name: shutil.which(name, path=system_path) for name in ("ffmpeg", "ffprobe")}
    if not all(found.values()):
        return None
    target = cache / "ffmpeg"
    (target / "bin").mkdir(parents=True, exist_ok=True)
    for name, path in found.items():
        shutil.copy2(str(path), target / "bin" / name)
    (cache / COPY_NOTE).write_text(f"a copy of the machine's {found['ffmpeg']}, because the download failed\n")
    return target


def check_bare(bwrap: str, bare: BareMachine, parent: Path) -> list[str]:
    """What is wrong with the bare machine, tried in a throwaway sandbox; [] when it holds."""
    env = {"PATH": SYSTEM_PATH, "LANG": "C.UTF-8"}

    def full_machine(script: str) -> bool:
        try:
            done = subprocess.run(["sh", "-c", script], env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    # Looked up on the machine as it is, not in `bare.bin_dirs`: a folder the bare machine forgot must show.
    folders = [Path(folder) for folder in SYSTEM_PATH.split(os.pathsep) if folder]
    present = [name for name in BARE_HIDDEN_PROGRAMS if any((folder / name).exists() for folder in folders)]
    lines = [f'command -v "{name}" >/dev/null 2>&1 && echo "found {name}"' for name in present]
    if full_machine("python3 -m pip --version"):
        lines.append('python3 -m pip --version >/dev/null 2>&1 || echo "nopip"')
    if full_machine('python3 -c "import PIL"'):
        lines.append('python3 -c "import PIL" >/dev/null 2>&1 && echo "pil"')
    lines.append("echo done")

    root = Path(tempfile.mkdtemp(prefix="w", dir=parent))
    try:
        sandbox = Sandbox.at(root)
        for folder in (sandbox.home, sandbox.work, sandbox.tmp):
            folder.mkdir(parents=True, exist_ok=True)
        wrapper = private_tmp_wrapper(bwrap, sandbox, bare_args(bare, sandbox, jzr=False))
        try:
            done = subprocess.run(
                [*wrapper, "sh", "-c", "\n".join(lines)],
                env={**env, "HOME": str(sandbox.home)},
                capture_output=True,
                text=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return [f"the bare machine could not be started: {error}"]
    finally:
        remove_sandbox(root)

    said = done.stdout.split("\n")
    if "done" not in said:
        detail = done.stderr.strip().splitlines()
        return ["the bare machine could not be started" + (f": {detail[-1]}" if detail else "")]
    problems = [f"{line[6:]} is still found on the bare machine" for line in said if line.startswith("found ")]
    if "nopip" in said:
        problems.append("pip does not work on the bare machine")
    if "pil" in said:
        problems.append("PIL can still be imported on the bare machine")
    return problems


def default_source() -> JzrSource:
    jzr_bin = REPO / ".venv" / "bin" / "jzr"
    return JzrSource(
        jzr_bin=jzr_bin,
        skills_dir=REPO / "src" / "jzr_tools" / "data" / "skill",
        mcp_command=[str(jzr_bin), "agent", "mcp"],
    )


def _read_records(folder: Path) -> list[dict]:
    return [json.loads(line) for line in (folder / "runs.jsonl").read_text().splitlines() if line.strip()]


def _summary_text(records: list[dict], meta: dict | None) -> str:
    prices = fit_prices(records)
    return render_markdown(summarize(records, prices), meta, prices)


def write_summary(out_dir: Path) -> Path:
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    target = out_dir / "summary.md"
    target.write_text(_summary_text(_read_records(out_dir), meta))
    return target


def combine_records(folders: Sequence[Path]) -> tuple[list[dict], dict[str, str]]:
    """For each task, the runs of the last folder that has it; and which folder (by name) that was."""
    per_folder = [(Path(folder), _read_records(Path(folder))) for folder in folders]
    sources: dict[str, str] = {}
    for folder, records in per_folder:
        for record in records:
            sources.pop(record["task"], None)  # a later folder moves the task to the end
            sources[record["task"]] = folder.name
    last = {task: index for index, (_, records) in enumerate(per_folder) for task in {r["task"] for r in records}}
    combined = [r for index, (_, records) in enumerate(per_folder) for r in records if last[r["task"]] == index]
    return combined, sources


def combined_summary(folders: Sequence[Path]) -> str:
    records, sources = combine_records(folders)
    return _summary_text(records, {"sources": sources})


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def run_all(args: Any) -> int:
    tasks = _split(args.tasks)
    arms = _split(args.arms)
    bare_arms = _split(getattr(args, "bare_arms", "") or "")
    try:
        task_objects = {name: get_task(name) for name in tasks}
    except KeyError as error:
        _say(f"error: {error.args[0]}")
        return 2
    unknown = [a for a in arms if a not in ARMS]
    if unknown or not (arms or bare_arms) or not tasks:
        _say(f"error: arms must be some of {', '.join(ARMS)}; tasks some of {', '.join(TASKS)}")
        return 2
    if any(a not in ARMS for a in bare_arms):
        _say(f"error: --bare-arms must be some of {', '.join(ARMS)}")
        return 2
    if bare_arms and getattr(args, "shared_tmp", False):
        _say("error: --bare-arms needs each run's own /tmp: the bare machine cannot be used with --shared-tmp")
        return 2

    found = find_jzr_on(SYSTEM_PATH)
    if found is not None:
        _say(f"error: {found} is on the system PATH, so the baseline would have jzr")
        return 2

    source = default_source()
    if not os.access(source.jzr_bin, os.X_OK):
        _say(f"error: {source.jzr_bin} is missing; run `uv sync --extra mcp` in {REPO}")
        return 2
    if "mcp" in (*arms, *bare_arms) and _output([str(REPO / ".venv" / "bin" / "python"), "-c", "import mcp"]) is None:
        _say("error: the MCP arm needs the mcp extra; run `uv sync --extra mcp`")
        return 2

    claude_bin = args.claude or shutil.which("claude")
    if not claude_bin:
        _say("error: claude is not on PATH; pass --claude")
        return 2

    bwrap: str | None = None
    if not getattr(args, "shared_tmp", False):
        bwrap, why = _find_bwrap()
        if bwrap is None:
            _say(f"error: {why}; each run needs it for its own /tmp. Install bubblewrap, or pass --shared-tmp")
            return 2

    bare = find_bare_machine() if bare_arms else None
    plan = plan_runs(tasks, arms, args.runs, args.seed, bare_arms)
    auth = pick_auth(os.environ)

    if args.dry_run:
        for planned in plan:
            print(planned.run_id)
        print(f"{len(plan)} runs")
        if plan:
            with tempfile.TemporaryDirectory(prefix="jzr-bench-dry-") as tmp:
                first = plan[0]
                setup = prepare_arm(
                    first.arm,
                    Path(tmp) / "sandbox",
                    source,
                    auth or _placeholder_auth(),
                    model=args.model,
                    max_turns=args.max_turns,
                    bwrap=bwrap,
                    bare=bare if first.machine == "bare" else None,
                )
                prompt = task_objects[first.task].prompt
                front = " ".join([*setup.wrapper, claude_bin])
                print(f"first command: {front} -p {prompt!r} {' '.join(setup.claude_args)}")
                print(f"environment: {', '.join(sorted(setup.env))}")
        if auth is None:
            print("note: no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY is set; a real run would stop here")
        return 0

    if auth is None:
        _say("error: set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY")
        return 2

    sandbox_parent = Path(args.sandbox_dir) if args.sandbox_dir else default_sandbox_parent()
    jzr_ffmpeg_note: str | None = None
    if bare is not None:
        assert bwrap is not None
        problems = check_bare(bwrap, bare, sandbox_parent)
        if problems:
            _say("error: the bare machine is not bare: " + "; ".join(problems))
            return 2
        if any(arm in JZR_ARMS for arm in bare_arms):
            try:
                ffmpeg_dir = ensure_jzr_ffmpeg(source.jzr_bin)
                note_file = ffmpeg_dir.parent / COPY_NOTE
                jzr_ffmpeg_note = (
                    note_file.read_text().strip() if note_file.is_file() else "downloaded by `jzr ffmpeg install`"
                )
            except RuntimeError as error:
                copied = copy_system_ffmpeg()
                if copied is None:
                    _say(f"error: the bare machine's jzr arms need jzr's own ffmpeg: {error}")
                    return 2
                ffmpeg_dir = copied
                jzr_ffmpeg_note = (copied.parent / COPY_NOTE).read_text().strip()
                _say(f"note: jzr's own ffmpeg on the bare machine is {jzr_ffmpeg_note}")
            bare = BareMachine(bin_dirs=bare.bin_dirs, site_dirs=bare.site_dirs, jzr_ffmpeg=ffmpeg_dir)

    out_dir = Path(args.out) if args.out else REPO / "bench" / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "runs.jsonl"
    runs_path.touch()

    meta: dict[str, Any] = {
        "started_at": _now(),
        "finished_at": None,
        "claude_code_version": _output([claude_bin, "--version"]),
        "jzr_version": _output([str(source.jzr_bin), "--version"]),
        "git_commit": _git_commit(),
        "model": args.model,
        "resolved_models": [],
        "tasks": tasks,
        "arms": arms,
        "bare_arms": bare_arms,
        "bare_jzr_ffmpeg": jzr_ffmpeg_note,
        "rounds": args.runs,
        "seed": args.seed,
        "max_turns": args.max_turns,
        "timeout_s": args.timeout,
        "max_cost_usd": args.max_cost_usd,
        "system_path": SYSTEM_PATH,
        "private_tmp": bwrap is not None,
        "auth": auth.var,
        "stopped": None,
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    for name in tasks:
        fixture_dir = out_dir / "fixtures" / name
        fixture_dir.mkdir(parents=True, exist_ok=True)
        if not any(fixture_dir.iterdir()):
            task_objects[name].prepare_fixtures(fixture_dir)

    spent = 0.0
    exit_code = 0
    _say(f"{len(plan)} runs, auth from {auth.var}, results in {out_dir}")
    try:
        for index, planned in enumerate(plan, 1):
            if spent >= args.max_cost_usd:
                meta["stopped"] = f"cost ceiling ${args.max_cost_usd:g} reached after {index - 1} runs"
                _say(meta["stopped"])
                exit_code = 2
                break
            # The agent reads this path, so the name says nothing about the benchmark, the task or the arm.
            root = Path(tempfile.mkdtemp(prefix="w", dir=sandbox_parent))
            try:
                setup = prepare_arm(
                    planned.arm,
                    root,
                    source,
                    auth,
                    model=args.model,
                    max_turns=args.max_turns,
                    bwrap=bwrap,
                    bare=bare if planned.machine == "bare" else None,
                )
                transcript = out_dir / "transcripts" / f"{planned.run_id}.jsonl"
                record = run_one(
                    planned,
                    task_objects[planned.task],
                    setup,
                    claude_bin=claude_bin,
                    fixture_dir=out_dir / "fixtures" / planned.task,
                    transcript_path=transcript,
                    timeout_s=args.timeout,
                )
            finally:
                if args.keep_sandboxes:
                    _say(f"  sandbox kept: {root} ({planned.run_id})")
                else:
                    remove_sandbox(root)
            record["transcript"] = str(transcript.relative_to(out_dir))
            if args.keep_sandboxes:
                record["sandbox"] = str(root)
            with runs_path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
            spent += record["total_cost_usd"] or 0.0
            if record["model"] and record["model"] not in meta["resolved_models"]:
                meta["resolved_models"].append(record["model"])
            _say(_progress(index, len(plan), record, spent))
            if record["auth_failed"]:
                meta["stopped"] = f"the API rejected the credential from {auth.var}; fix it and run again"
                _say(meta["stopped"])
                exit_code = 2
                break
    except KeyboardInterrupt:
        meta["stopped"] = "interrupted"
        exit_code = 130
    finally:
        meta["finished_at"] = _now()
        meta["spent_usd"] = round(spent, 6)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        summary = write_summary(out_dir)
        print(summary.read_text())
        _say(f"summary: {summary}")
    return exit_code


def _placeholder_auth():
    from .arms import API_KEY_VAR, Auth

    return Auth(API_KEY_VAR, "<not set>")


def _progress(index: int, total: int, record: dict, spent: float) -> str:
    status = "pass" if record["passed"] else ("timeout" if record["timed_out"] else "FAIL")
    cost = record["total_cost_usd"]
    jzr = [k for k in ("cli", "skill", "mcp") if record[f"used_jzr_{k}"]]
    parts = [
        f"[{index}/{total}] {record['run_id']}: {status}",
        f"${cost:.4f}" if cost is not None else "$?",
        f"{record['num_turns']} turns",
        f"jzr: {'+'.join(jzr) or 'no'}",
    ]
    if not record["isolation_ok"]:
        parts.append("ISOLATION: " + "; ".join(record["isolation_problems"]))
    if record["error"]:
        parts.append(record["error"][:120])
    elif not record["passed"]:
        parts.append("; ".join(record["check_details"])[:160])
    parts.append(f"spent ${spent:.2f}")
    return " · ".join(parts)
