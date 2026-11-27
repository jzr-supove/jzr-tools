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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .arms import ARMS, SYSTEM_PATH, ArmSetup, JzrSource, find_jzr_on, pick_auth, prepare_arm
from .plan import PlannedRun, plan_runs
from .report import render_markdown, summarize
from .split_skill import write_split
from .stream import check_isolation, parse_stream
from .tasks import TASKS, Task, get_task

REPO = Path(__file__).resolve().parents[2]
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
    command = [claude_bin, "-p", task.prompt, *arm_setup.claude_args]

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


def default_source() -> JzrSource:
    jzr_bin = REPO / ".venv" / "bin" / "jzr"
    return JzrSource(
        jzr_bin=jzr_bin,
        skill_dir=REPO / "src" / "jzr_tools" / "data" / "skill" / "jzr",
        mcp_command=[str(jzr_bin), "agent", "mcp"],
    )


def write_summary(out_dir: Path) -> Path:
    records = [json.loads(line) for line in (out_dir / "runs.jsonl").read_text().splitlines() if line.strip()]
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    target = out_dir / "summary.md"
    target.write_text(render_markdown(summarize(records), meta))
    return target


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def run_all(args: Any) -> int:
    tasks = _split(args.tasks)
    arms = _split(args.arms)
    try:
        task_objects = {name: get_task(name) for name in tasks}
    except KeyError as error:
        _say(f"error: {error.args[0]}")
        return 2
    unknown = [a for a in arms if a not in ARMS]
    if unknown or not arms or not tasks:
        _say(f"error: arms must be some of {', '.join(ARMS)}; tasks some of {', '.join(TASKS)}")
        return 2

    found = find_jzr_on(SYSTEM_PATH)
    if found is not None:
        _say(f"error: {found} is on the system PATH, so the baseline would have jzr")
        return 2

    source = default_source()
    if not os.access(source.jzr_bin, os.X_OK):
        _say(f"error: {source.jzr_bin} is missing; run `uv sync --extra mcp` in {REPO}")
        return 2
    if "mcp" in arms and _output([str(REPO / ".venv" / "bin" / "python"), "-c", "import mcp"]) is None:
        _say("error: the MCP arm needs the mcp extra; run `uv sync --extra mcp`")
        return 2

    claude_bin = args.claude or shutil.which("claude")
    if not claude_bin:
        _say("error: claude is not on PATH; pass --claude")
        return 2

    plan = plan_runs(tasks, arms, args.runs, args.seed)
    auth = pick_auth(os.environ)

    if args.dry_run:
        for planned in plan:
            print(planned.run_id)
        print(f"{len(plan)} runs")
        if plan:
            with tempfile.TemporaryDirectory(prefix="jzr-bench-dry-") as tmp:
                first = plan[0]
                if first.arm == "split":
                    write_split(Path(tmp) / "split-skills")
                    source = replace(source, split_dir=Path(tmp) / "split-skills")
                setup = prepare_arm(
                    first.arm,
                    Path(tmp) / "sandbox",
                    source,
                    auth or _placeholder_auth(),
                    model=args.model,
                    max_turns=args.max_turns,
                )
                prompt = task_objects[first.task].prompt
                print(f"first command: {claude_bin} -p {prompt!r} {' '.join(setup.claude_args)}")
                print(f"environment: {', '.join(sorted(setup.env))}")
        if auth is None:
            print("note: no CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY is set; a real run would stop here")
        return 0

    if auth is None:
        _say("error: set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY")
        return 2

    out_dir = Path(args.out) if args.out else REPO / "bench" / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "runs.jsonl"
    runs_path.touch()
    sandbox_parent = Path(args.sandbox_dir) if args.sandbox_dir else Path(tempfile.gettempdir())

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
        "rounds": args.runs,
        "seed": args.seed,
        "max_turns": args.max_turns,
        "timeout_s": args.timeout,
        "max_cost_usd": args.max_cost_usd,
        "system_path": SYSTEM_PATH,
        "auth": auth.var,
        "stopped": None,
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    if "split" in arms:
        split_dir = out_dir / "split-skills"
        if not split_dir.exists():
            write_split(split_dir)
        source = replace(source, split_dir=split_dir)

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
            root = Path(tempfile.mkdtemp(prefix=f"jzr-bench-{planned.run_id}-", dir=sandbox_parent))
            try:
                setup = prepare_arm(planned.arm, root, source, auth, model=args.model, max_turns=args.max_turns)
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
                    _say(f"  sandbox kept: {root}")
                else:
                    shutil.rmtree(root, ignore_errors=True)
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
