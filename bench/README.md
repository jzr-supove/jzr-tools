# Agent token benchmark

Does an AI agent spend fewer tokens and dollars on a job when it has jzr? This
folder runs the same tasks through Claude Code in three setups ("arms") and
compares what each run cost, counting only runs whose result was correct.

It is a development tool: not part of the package, not run by CI.

| Arm | What Claude Code gets |
|---|---|
| `baseline` | No jzr. The machine's own ffmpeg, ImageMagick and python3 are there. |
| `skill` | `jzr` on PATH and the four jzr skills (`jzr-media`, `jzr-pdf`, `jzr-data`, `jzr-system`), as `jzr agent install` sets up |
| `mcp` | The jzr MCP server only, as `jzr agent install --mcp` would add it, without the skills |

Results from before the four skills have a `skill` arm with the single `jzr`
skill of jzr 0.3.0 and a `split` arm, an experiment that became today's
`skill` arm; `report` still reads them.

## How a run is isolated

Every run starts Claude Code (`claude -p`) in a fresh folder with its own
`HOME` and `CLAUDE_CONFIG_DIR`, so your `~/.claude` (CLAUDE.md, memory, skills,
plugins, MCP servers) is never seen. The environment is built from a short
allowlist instead of being inherited, MCP servers are limited with
`--strict-mcp-config`, and each run's `init` event from Claude Code is checked:
a baseline run that somehow saw a jzr skill or tool is flagged and left out of
the medians.

Every run also gets its own empty `/tmp` (through bubblewrap), so a file one
run leaves there cannot change a later run or stay on your machine. The rest of
the file system is the machine's own. Sandboxes are made in `/var/tmp` under
names that say nothing about the benchmark, the task or the arm, because the
agent reads its working folder's path. `--shared-tmp` turns the private `/tmp`
off for machines without bubblewrap; the summary says which one a run used.

`--bare` is not used: it cuts Claude Code down to three tools and a shorter
system prompt, which is not what people run.

## The bare machine

`--bare-arms baseline,skill` runs those arms a second time on a machine
without ffmpeg, ImageMagick, Ghostscript, poppler, LibreOffice, exiftool and
the like, and with a Python that has only its standard library and pip. The
programs and libraries are hidden inside the run's own mount namespace (an
overlay of whiteouts over the PATH folders, an empty folder over the system
site-packages); your machine is not changed. `pip install` and downloads still
work there, system package managers do not (no password for `sudo`), so this
is a machine where the agent cannot become administrator.

The jzr arms get jzr's own ffmpeg on the bare machine, as after
`jzr ffmpeg install`: downloaded once into `bench/results/.cache/`, or, when
the download fails, a copy of the machine's ffmpeg put in the same place (the
summary's meta says which). Before the first run the runner checks that the
bare machine really lacks the tools, and stops if it does not. A copy of a
hidden program outside the PATH folders is not hidden.

The summary has one set of tables per machine; nothing is compared across
machines.

## Setup

From the repository root:

```
uv sync --extra mcp          # the MCP arm runs the repo's own `jzr agent mcp`
```

`bwrap` (the bubblewrap package) must be on PATH, unless you pass
`--shared-tmp`.

Auth, one of:

```
claude setup-token                       # subscription: prints a token
export CLAUDE_CODE_OAUTH_TOKEN=...       # used first when set

export ANTHROPIC_API_KEY=sk-ant-...      # API billing
```

With a subscription, `total_cost_usd` is Claude Code's estimate at API prices,
not a charge.

## Running

```
uv run python bench/run.py tasks                          # list the tasks
uv run python bench/run.py run --dry-run                  # show the plan, start nothing
uv run python bench/run.py run --tasks hello,gif --runs 3 --model sonnet --max-cost-usd 5
uv run python bench/run.py run --tasks hello,gif,videosize --bare-arms baseline,skill   # also on the bare machine
uv run python bench/run.py report bench/results/<folder>  # rebuild summary.md
uv run python bench/run.py report <folder> <folder> ...   # one summary for several runs
```

With several folders, each task is taken from the last folder listed that ran
it. The summary names the folder of each task and is only printed (`--out FILE`
also writes it).

Runs are interleaved (each round runs every arm once per task, in a shuffled
order) so prompt caching between consecutive runs does not favour one arm. The
run stops before the next run once `--max-cost-usd` is reached.

Each results folder (`bench/results/<UTC time>/`, not committed) has
`meta.json` (versions, model, arms), `runs.jsonl` (one record per run),
`transcripts/` (Claude Code's raw stream-json output) and `summary.md`.

## The tasks

`hello`, `gif`, `heic` and `pdf` run by default. The others are held out and
run by name. `compress` and `xlsx` were held out at first and have since been
used for tuning. `videosize`, `tags`, `pdfsplit`, `batch`, `xlsxjson` and
`rename` are jobs of the kind jzr was built for (a video to a size limit, MP3
tags with cover art, a PDF in page groups, a folder tree of mixed images, a
workbook to JSON, renaming by date): report them next to `gif` and `compress`,
which the model solves with one command it knows, so the set is not read as a
fair sample of all jobs.

## Reading the numbers

- **Cost** is the headline: a token written to the cache costs many times one
  re-read from it, so a raw token total misleads. The token columns are shown
  beside it.
- **Cost of passed runs** is the fair comparison; a cheap wrong answer does not
  count. Pass rate is reported separately. **Range** is the cheapest and the
  dearest passed run: when it is wide, three runs do not pin the median down.
- **`hello`** needs no tool. Its cost per arm is what starting a session costs
  with the skill description or the MCP tool names in context.
- **Minus hello** is the task's cost less the same arm's `hello` cost: what the
  job itself cost. More than half of a short run is the session start, which is
  the same in every arm, so the plain ratio makes the arms look closer than
  the jobs are. It needs `hello` among the tasks.
- **Written $, re-read $, output $** split the cost by kind of token: text that
  entered the context, context re-read on later turns, and what the model
  wrote. The prices are fitted from the runs' own costs and listed in the
  summary; when no exact fit exists the three columns show `–`.
- **All tasks** sums the per-task values per arm. **Unused, per session** is the
  arm's `hello` cost less the baseline's: what having jzr installed adds to a
  session that never uses it.
- **Used jzr** says whether the agent actually ran jzr. A `skill` arm that
  rarely uses it is a finding about the skill's description.

Tests (offline, never start Claude Code):

```
uv run python -m unittest discover -s bench/tests -v
```
