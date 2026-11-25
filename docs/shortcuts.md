# Shortcuts: `jzr bind`

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)


Save a long command under a short name, then run it as `jzr <name>`:

```bash
jzr bind add tunnel ngrok http 5173 --url https://example.ngrok-free.dev
jzr tunnel                     # runs the command
jzr tunnel --log stdout        # anything after the name is added to it
jzr bind list
jzr bind remove tunnel
```

The command can also be given as one quoted string; both forms store the same
thing:

```bash
jzr bind add tunnel "ngrok http 5173 --url https://example.ngrok-free.dev"
```

## Arguments in the middle

Words typed after the name go on the end of the command. To put them
somewhere else, write `{1}`, `{2}` and so on where they belong, and `{*}`
for all the words after the highest number:

```bash
jzr bind add ci git commit -m '{1}' --no-verify
jzr ci "fix the typo"          # git commit -m "fix the typo" --no-verify

jzr bind add logs ssh web tail -n '{1}' '/var/log/{2}.log'
jzr logs 50 nginx              # ssh web tail -n 50 /var/log/nginx.log

jzr bind add lg git log '{*}' --oneline
jzr lg -5 main                 # git log -5 main --oneline
jzr lg                         # git log --oneline
```

- Each value goes in as one word, spaces included, and `{1}` also works
  inside a word, as in `--message={1}`.
- A shortcut with `{2}` needs at least two words. With fewer, nothing runs:
  `ci needs 1 argument (got 0): git commit -m {1} --no-verify` (exit `2`).
- Words beyond the highest number go on the end, or where `{*}` is.
- Only `{1}`, `{2}`, ... and `{*}` are special. Other braces, as in
  `{"a": 1}` or `find -exec ... {}`, stay as they are. `{{1}}` is the plain
  text `{1}`.
- In PowerShell, quote the placeholders (`'{1}'`) or give the whole command
  as one quoted string. Unquoted, PowerShell turns `{1}` into a script
  block, and `jzr bind add` refuses what it receives and says so.

## Other commands and details

Other commands: `jzr bind add --force NAME ...` replaces an existing shortcut,
`jzr bind run NAME` is the long way to run one, `jzr bind path` prints where
they are stored, and `jzr bind list --json` is for scripts.

Behavior worth knowing:

- The command runs directly, without a shell, so it behaves the same on
  Linux, macOS and Windows. The price: no pipes, `&&`, `$VARIABLES` or `~`.
  One shortcut is one command.
- Everything after the name belongs to the command, including `--options`,
  both when binding and when running. `--force` therefore goes before the
  name, and `jzr tunnel --help` shows ngrok's help, not jzr's.
- Built-in commands always win. `jzr bind add host ...` is refused, and if a
  later version of jzr adds a command with the name of one of your
  shortcuts, `jzr bind list` marks it and `jzr bind run NAME` still reaches
  it.
- Names start with a letter or digit and may contain letters, digits, `.`,
  `_` and `-`.
- The exit code is the command's own. Ctrl+C goes to the command, and jzr
  waits for it to finish. A program that is not installed gives exit `127`.
- Shortcuts live in `bindings.json` in jzr's config folder: `~/.config/jzr`
  (or `$XDG_CONFIG_HOME/jzr`), `%APPDATA%\jzr` on Windows, or wherever
  `JZR_CONFIG_DIR` points. It is plain JSON that you can edit or copy to
  another machine. It is only readable by you, but anything in it, tokens
  included, is stored as plain text.
- A bindings file that is damaged is reported and never overwritten.
- `jzr -h` does not list your shortcuts; `jzr bind list` does.

On Windows:

- What you type reaches the command as typed: `$HOME`, `%PATH%`, `~` and
  `*.txt` are not expanded by jzr (other jzr commands do expand wildcards,
  since cmd and PowerShell leave that to the program).
- One quoted string is split by Windows rules: double quotes group words and
  backslashes in paths are kept; single quotes are ordinary characters.
  `jzr bind list` writes commands back the same way.
- Programs that are `.cmd` or `.bat` files, such as `npm`, `pnpm` and `yarn`,
  have to run through cmd.exe. jzr quotes every argument so `&`, `|`, `<` and
  `>` stay literal, and refuses to run one with `%`, `"`, `!` or a line
  break in it, which cmd.exe would act on whatever the quoting.

Tried on Linux and Windows 11.
