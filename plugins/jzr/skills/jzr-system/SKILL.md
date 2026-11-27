---
name: jzr-system
description: Run the jzr command line for machine jobs. Find or free a port, see what holds it, clean caches, and set up shell completion.
---

# jzr-system

Part of jzr, a command-line tool for everyday file and machine jobs. If `jzr` is not found, run it as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools`).

## Commands

Each line is a command with its own arguments and options, then what it does. Run `jzr <command> -h` only when an option's meaning is not clear; `reference/<group>.md` has worked examples.

### port
- `jzr port kill PORT... --restart-hns` - Force-kill processes using a local port (Windows, Linux, and macOS).
- `jzr port show [PORT]... --udp --json` - Show which processes are using local ports.
### host
- `jzr host status --live --interval SECONDS --detail --json` - Show host, CPU, memory, storage and GPU status.
- `jzr host clean --yes --only ID[,ID...] --json` - Find caches and other disposable data, and remove what you pick.
- `jzr host procs [PATTERN] --top N --each --kill --force --json` - Show what uses the most memory, grouped by application.
### bind
- `jzr bind add NAME [COMMAND]... --force` - Bind NAME to COMMAND, so that `jzr NAME` runs it.
- `jzr bind list --json` - List the bindings.
- `jzr bind path` - Print where the bindings are stored.
- `jzr bind remove NAME...` - Remove bindings.
- `jzr bind run NAME [ARGS]...` - Run the binding NAME, with ARGS added to its command.
### completion
- `jzr completion bash` - Print the bash completion script.
- `jzr completion fish` - Print the fish completion script.
- `jzr completion install [SHELL]` - Set up completion for SHELL, or for the shell you are typing in.
- `jzr completion powershell` - Print the powershell completion script.
- `jzr completion remove [SHELL]` - Undo `jzr completion install` for SHELL, or for the current shell.
- `jzr completion zsh` - Print the zsh completion script.

## Rules

- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others.
- `jzr port kill` ends processes without asking. Run `jzr port show PORT` first and tell the user what will end.
- `jzr host clean` and `jzr host procs --kill` ask on a terminal and then delete or end what the user picks: suggest them instead of running them. `jzr host clean --dry-run` and `jzr host procs` without `--kill` only show.
- Ask before `jzr bind add`, `jzr bind remove`, `jzr completion install` or `jzr completion remove`: they change the user's own setup.
- Do not guess options: an unknown one exits 2.
