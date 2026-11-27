# Agent token benchmark

Does an AI agent spend fewer tokens and dollars on a job when it has jzr? This
folder runs the same tasks through Claude Code in four setups ("arms") and
compares what each run cost, counting only runs whose result was correct.

It is a development tool: not part of the package, not run by CI.

| Arm | What Claude Code gets |
|---|---|
| `baseline` | No jzr. The machine's own ffmpeg, ImageMagick and python3 are there. |
| `skill` | `jzr` on PATH and the jzr skill, as `jzr agent install` sets up |
| `mcp` | The jzr MCP server only, as `jzr agent install --mcp` would add it, without the skill |
| `split` | Experiment only: the skill cut into `jzr-media`, `jzr-pdf`, `jzr-data` and `jzr-system` (written to `<results>/split-skills/`), jzr on PATH, no MCP; jzr itself never installs these |

## How a run is isolated

Every run starts Claude Code (`claude -p`) in a fresh folder with its own
`HOME` and `CLAUDE_CONFIG_DIR`, so your `~/.claude` (CLAUDE.md, memory, skills,
plugins, MCP servers) is never seen. The environment is built from a short
allowlist instead of being inherited, MCP servers are limited with
`--strict-mcp-config`, and each run's `init` event from Claude Code is checked:
a baseline run that somehow saw a jzr skill or tool is flagged and left out of
the medians.

`--bare` is not used: it cuts Claude Code down to three tools and a shorter
system prompt, which is not what people run.

## Setup

From the repository root:

```
uv sync --extra mcp          # the MCP arm runs the repo's own `jzr agent mcp`
```

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
uv run python bench/run.py report bench/results/<folder>  # rebuild summary.md
```

Runs are interleaved (each round runs every arm once per task, in a shuffled
order) so prompt caching between consecutive runs does not favour one arm. The
run stops before the next run once `--max-cost-usd` is reached.

Each results folder (`bench/results/<UTC time>/`, not committed) has
`meta.json` (versions, model, arms), `runs.jsonl` (one record per run),
`transcripts/` (Claude Code's raw stream-json output) and `summary.md`.

## Reading the numbers

- **Cost** is the headline: cache reads are billed far below fresh input, so
  a raw token total misleads. The token columns are shown beside it.
- **Cost of passed runs** is the fair comparison; a cheap wrong answer does not
  count. Pass rate is reported separately.
- **`hello`** needs no tool. Its cost per arm is the fixed overhead of having
  the skill description or the MCP tool definitions in context.
- **Used jzr** says whether the agent actually ran jzr. A `skill` arm that
  rarely uses it is a finding about the skill's description.

Tests (offline, never start Claude Code):

```
uv run python -m unittest discover -s bench/tests -v
```
