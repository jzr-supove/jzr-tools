# Shell completion

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](agents.md)


Tab can complete jzr's commands, options and choices in bash, zsh, fish and
PowerShell. Set it up for the shell you are typing in:

```bash
jzr completion install
```

It finds the shell from the process that started jzr (name one to choose:
`jzr completion install zsh`), writes the completion script to jzr's data
folder, and adds one marked block to the shell's start-up file that loads
it: `~/.bashrc`, `~/.zshrc`, or PowerShell's `profile.ps1`. fish gets
`~/.config/fish/completions/jzr.fish` and no start-up change. Running it
again changes nothing; `--dry-run` shows what it would do, and
`jzr completion remove` takes it out again. It warns, without changing
anything, when PowerShell's execution policy keeps the profile from
running (Windows PowerShell's default) or when `~/.bash_profile` does not
load `~/.bashrc`. After upgrading jzr, run `jzr completion install` again.

To load the script yourself instead, `jzr completion SHELL` prints it:

```bash
echo 'eval "$(jzr completion bash)"' >> ~/.bashrc
echo 'eval "$(jzr completion zsh)"' >> ~/.zshrc
jzr completion fish > ~/.config/fish/completions/jzr.fish
```

```powershell
Add-Content $PROFILE 'jzr completion powershell | Out-String | Invoke-Expression'
```

Open a new shell afterwards. File arguments fall back to the shell's own path
completion. Your `jzr bind` shortcuts are not completed. Windows PowerShell 5.1
does not offer completions for a bare `-` or `--`; type the first letter of
the option.
