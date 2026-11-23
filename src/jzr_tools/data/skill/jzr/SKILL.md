---
name: jzr
description: Run the jzr command line for everyday file and machine jobs. Convert, compress, resize, trim or inspect images, video and audio; make GIFs; edit audio tags; convert JSON, YAML, CSV, TOML and Excel data; rename files; free a port; hash files; encode, diff or slug text; read JWTs; convert timestamps; make UUIDs or passwords. Use it instead of ffmpeg or Pillow scripts.
---

# jzr

One command-line tool for small everyday jobs. If `jzr --version` fails, jzr is not installed: tell the user (`uv tool install jzr-tools` or `pipx install jzr-tools`).

## Commands

The list shows no options. Before using one, run `jzr <group> <command> -h`, or read the linked reference file, which also has worked examples.

<!-- commands:start -->
### image ([reference/image.md](reference/image.md))
- `jzr image compress` - Make image files smaller, optionally to a target size.
- `jzr image convert` - Convert images to another format.
- `jzr image formats` - Show supported extensions and the conversion engines available.
- `jzr image info` - Show the format, size, colour profile and metadata of images.
- `jzr image resize` - Make images smaller (or, with --upscale, larger).
### video ([reference/video.md](reference/video.md))
- `jzr video compress` - Make video files smaller, optionally to a target size.
- `jzr video convert` - Convert video files to another container, or to audio.
- `jzr video formats` - Show the target formats, the ffmpeg in use and its encoders.
- `jzr video gif` - Make animated GIFs from video files.
- `jzr video info` - Show the container, duration, size and streams of video files.
- `jzr video trim` - Cut a time range out of video files.
### audio ([reference/audio.md](reference/audio.md))
- `jzr audio convert` - Convert audio files to another format.
- `jzr audio formats` - Show the target formats, the ffmpeg in use and its audio encoders.
- `jzr audio info` - Show the format, duration, streams and tags of audio files.
- `jzr audio tags` - Show or edit the tags, cover art and lyrics of audio files.
- `jzr audio trim` - Cut a time range out of audio files.
### ffmpeg ([reference/ffmpeg.md](reference/ffmpeg.md))
- `jzr ffmpeg install` - Download ffmpeg and ffprobe into jzr's own folder.
- `jzr ffmpeg remove` - Delete the ffmpeg that `jzr ffmpeg install` downloaded.
- `jzr ffmpeg status` - Show which ffmpeg and ffprobe jzr uses and where they come from.
### file ([reference/file.md](reference/file.md))
- `jzr file rename` - Rename many files at once; shows the plan unless --apply is given.
### text ([reference/text.md](reference/text.md))
- `jzr text base64` - Encode text or a file as Base64, or decode it with -d.
- `jzr text diff` - Show how text file NEW differs from OLD, as a unified diff.
- `jzr text slug` - Convert text into a URL-safe slug, lowercase kebab-case by default.
- `jzr text url` - Percent-encode text for a URL, decode it with -d, or --parse a URL.
### tools ([reference/tools.md](reference/tools.md))
- `jzr uuid` - Generate UUIDs (v4, or v7 with --v7).
- `jzr password` - Generate passwords or passphrases.
- `jzr hash` - Hash files or text (SHA-256 by default).
- `jzr timestamp` - Convert between Unix time and dates.
- `jzr jwt` - Decode a JWT and check its times and HMAC signature.
### random ([reference/random.md](reference/random.md))
- `jzr random int` - Random whole numbers from MIN to MAX, both included (default 1 100).
- `jzr random pick` - Pick random ITEMS; without ITEMS, the lines of stdin.
- `jzr random shuffle` - Print ITEMS in random order; without ITEMS, the lines of stdin.
### data ([reference/data.md](reference/data.md))
- `jzr data convert` - Convert data between JSON, YAML, CSV, TOML and Excel.
- `jzr data json` - Pretty-print, minify or check JSON from FILES or stdin.
### env ([reference/env.md](reference/env.md))
- `jzr env compare` - Show the keys only in A (default .env.example) or only in B (default .env).
- `jzr env example` - Print the keys of FILE (default .env) as KEY= lines for a .env.example.
### port ([reference/port.md](reference/port.md))
- `jzr port kill` - Force-kill processes using a local port (Windows, Linux, and macOS).
- `jzr port show` - Show which processes are using local ports.
### host ([reference/host.md](reference/host.md))
- `jzr host status` - Show host, CPU, memory, storage and GPU status.
- `jzr host clean` - Find caches and other disposable data, and remove what you pick.
- `jzr host procs` - Show what uses the most memory, grouped by application.
### bind ([reference/bind.md](reference/bind.md))
- `jzr bind add` - Bind NAME to COMMAND, so that `jzr NAME` runs it.
- `jzr bind list` - List the bindings.
- `jzr bind path` - Print where the bindings are stored.
- `jzr bind remove` - Remove bindings.
- `jzr bind run` - Run the binding NAME, with ARGS added to its command.
### completion ([reference/completion.md](reference/completion.md))
- `jzr completion bash` - Print the bash completion script.
- `jzr completion fish` - Print the fish completion script.
- `jzr completion install` - Set up completion for SHELL, or for the shell you are typing in.
- `jzr completion powershell` - Print the powershell completion script.
- `jzr completion remove` - Undo `jzr completion install` for SHELL, or for the current shell.
- `jzr completion zsh` - Print the zsh completion script.
<!-- commands:end -->

## Rules

- Sources stay as they are. Image, video and audio commands write a new file beside each source (`a.jpg`, `a_compressed.jpg`, `a_1600x1200.jpg`, `a_trim.mp4`) and print `source -> target` for each.
- An output that already exists is skipped. Pass `--overwrite` only when the user wants it replaced.
- `-o FILE` names the output of one source. `--outdir DIR` collects the outputs of many.
- With no SOURCES, the batch commands take every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters. Run such a scan with `--dry-run` first.
- `jzr file rename` only shows its plan; `-y` applies it.
- These change files in place: `jzr audio tags` with an editing option, `jzr data json -w`, `jzr file rename -y`.
- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others. `jzr text diff` and `jzr env compare` exit 1 when they find a difference, 2 when a file cannot be read.
- Quote paths with spaces, regexes and templates.
- Video commands and most audio commands need ffmpeg; `jzr ffmpeg status` says whether there is one.
- Never put a secret on the command line: `jzr jwt` takes it with `--secret-env` or `--secret-file`.
- `jzr port kill` ends processes without asking. Run `jzr port show PORT` first and tell the user what will end.

## Leave these to the user

They ask questions on a terminal, download or delete, so suggest the command instead of running it:

- `jzr host clean` removes the caches the user picks. `jzr host clean --dry-run` is safe to run.
- `jzr host procs --kill` ends the processes the user picks. Without `--kill` it only shows them.
- `jzr ffmpeg install` downloads ffmpeg.

Ask before `jzr bind add`, `jzr bind remove`, `jzr completion install`, `jzr completion remove` or `jzr ffmpeg remove`: they change the user's own setup.

## When not to use jzr

jzr does not crop, rotate, join, watermark or draw on images or video, and does not edit PDFs or spreadsheets. Use another tool for those. Do not guess options: an unknown one exits 2.
