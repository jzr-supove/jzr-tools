# jzr completion

## jzr completion bash

```text
Usage: jzr completion bash [OPTIONS]

  Print the bash completion script.
```

Examples:

- `jzr completion bash` - prints the script

## jzr completion fish

```text
Usage: jzr completion fish [OPTIONS]

  Print the fish completion script.
```

Examples:

- `jzr completion fish` - prints the script

## jzr completion install

```text
Usage: jzr completion install [OPTIONS] [SHELL]

  Set up completion for SHELL, or for the shell you are typing in.

  SHELL is bash, zsh, fish or powershell. The script is written to jzr's data folder
  (fish: its completions folder), and one marked block in the shell's start-up file
  loads it. Running it again changes nothing.

Options:
  --dry-run   Show what would change; change nothing.
```

Examples:

- `jzr completion install --dry-run` - shows what would change

## jzr completion powershell

```text
Usage: jzr completion powershell [OPTIONS]

  Print the powershell completion script.
```

Examples:

- `jzr completion powershell` - prints the script

## jzr completion remove

```text
Usage: jzr completion remove [OPTIONS] [SHELL]

  Undo `jzr completion install` for SHELL, or for the current shell.

Options:
  --dry-run   Show what would change; change nothing.
```

Examples:

- `jzr completion remove --dry-run` - shows what would change

## jzr completion zsh

```text
Usage: jzr completion zsh [OPTIONS]

  Print the zsh completion script.
```

Examples:

- `jzr completion zsh` - prints the script
