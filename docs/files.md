# Renaming files

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](ai-agents.md)


`jzr file rename` shows what it would do and changes nothing until you add
`-y` (`--apply`):

```bash
jzr file rename *.JPG --lower-ext                       # IMG_0412.JPG -> IMG_0412.jpg
jzr file rename -d photos --template "{date}-{n:03}"    # IMG_0412.JPG -> 2026-11-18-001.JPG
jzr file rename -d notes --case slug -y                 # "Встреча Notes.md" -> vstrecha-notes.md
jzr file rename --replace " (1)" "" -p "\(1\)" -y       # drop " (1)" from copies
jzr file rename 01_intro.txt --regex "^(\d+)_(.*)" "\2_\1"  # -> intro_01.txt
```

List files, or scan a folder with `-d` (`.` by default), `-p REGEX` and
`-r`. A scan leaves out hidden files and folders unless you add `--hidden`.
Files always stay in their own folder.

The name is built from the old one without its extension, in this order
whatever the order on the command line: `--replace OLD NEW` (`-i` to ignore
case), `--regex PATTERN REPL`, `--case lower|upper|title|slug|snake`,
`--prefix`/`--suffix`, then `--template` with the fields `{name}` (the name
so far), `{n}` (a number, `{n:03}` pads it), `{parent}` (folder name),
`{date}` (modified date) and `{ext}`. The extension stays unless you give
`--ext EXT` (`--ext ""` removes it) or `--lower-ext`.

`{n}` counts from `--start` (1) in the order the files were listed, or by
name for a scan, with `img2` before `img10`; `--sort name|mtime|size` and
`--reverse` change that. With `-r` each folder counts on its own.

Nothing is overwritten. Two files that would get the same name get `-2`,
`-3`, ...; a name that another file already has is skipped. Swaps (`a` and
`b` trade names), chains and case-only changes work, because files go through
a temporary name first. Names that Windows cannot hold (`?`, `:`, a trailing
dot, `CON`, ...) fail on every system, so renamed files still copy to
Windows. `--json` prints the plan or the result for scripts, `-v` also lists
the files whose name stays. The exit code is `1` if any file failed.
