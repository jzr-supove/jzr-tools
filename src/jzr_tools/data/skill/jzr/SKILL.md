---
name: jzr
description: Run the jzr command line for everyday file and machine jobs. Convert, compress, resize, trim or inspect images, video and audio; make GIFs; edit audio tags; merge, split or rotate PDFs; convert JSON, YAML, CSV, TOML and Excel data; rename files; free a port; hash files; encode, diff or slug text; read JWTs; convert timestamps; make UUIDs or passwords. Use it instead of ffmpeg or Pillow scripts.
---

# jzr

One command-line tool for small everyday jobs. If `jzr --version` fails, jzr is not installed: run it without installing as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools` or `pipx install jzr-tools`).

## Commands

Each line shows the command's own arguments and options; the options shared by the batch commands are explained under Rules. Run `jzr <group> <command> -h`, or read the linked reference file, which has worked examples, only when an option's meaning or format is not clear.

<!-- commands:start -->
### image ([reference/image.md](reference/image.md))
- `jzr image compress` - Make image files smaller, optionally to a target size. `[SOURCES]...` `--to [jpg|png|webp|avif|heic]` `--quality N` `--size SIZE` `--max-side N` `--strip` `--background COLOR`
- `jzr image convert` - Convert images to another format. `[SOURCES]...` `--to TEXT` `--from TEXT` `--quality N` `--backend [auto|pillow|magick]` `--background COLOR`
- `jzr image formats` - Show supported extensions and the conversion engines available.
- `jzr image info` - Show the format, size, colour profile and metadata of images. `FILES...` `--json` `--no-gps`
- `jzr image resize` - Make images smaller (or, with --upscale, larger). `[SOURCES]...` `--width N` `--height N` `--percent P` `--max-side N` `--upscale` `--to TEXT` `--quality N` `--strip` `--background COLOR`
- `jzr image strip` - Remove metadata from JPEG, PNG and WebP files without re-encoding. `[SOURCES]...` `--in-place` `--no-color-profile`
### video ([reference/video.md](reference/video.md))
- `jzr video compress` - Make video files smaller, optionally to a target size. `[SOURCES]...` `--quality [high|balanced|small]` `--size SIZE` `--for [email|discord]` `--resize N` `--no-audio` `--strip`
- `jzr video convert` - Convert video files to another container, or to audio. `[SOURCES]...` `--to TEXT` `--from TEXT` `--codec [h264|hevc|av1|vp9]` `--quality [high|balanced|small]` `--crf N` `--resize N` `--fps N` `--no-audio` `--bitrate TEXT`
- `jzr video formats` - Show the target formats, the ffmpeg in use and its encoders.
- `jzr video gif` - Make animated GIFs from video files. `[SOURCES]...` `--start TIME` `--end TIME` `--duration TIME` `--width N` `--fps N`
- `jzr video info` - Show the container, duration, size and streams of video files. `FILES...` `--json`
- `jzr video trim` - Cut a time range out of video files. `[SOURCES]...` `--start TIME` `--end TIME` `--duration TIME` `--fast`
### audio ([reference/audio.md](reference/audio.md))
- `jzr audio convert` - Convert audio files to another format. `[SOURCES]...` `--to TEXT` `--from TEXT` `--bitrate TEXT`
- `jzr audio formats` - Show the target formats, the ffmpeg in use and its audio encoders.
- `jzr audio info` - Show the format, duration, streams and tags of audio files. `FILES...` `--json`
- `jzr audio tags` - Show or edit the tags, cover art and lyrics of audio files. `FILES...` `--json` `--set KEY=VALUE` `--unset KEY` `--clear` `--cover IMAGE` `--no-cover` `--lyrics FILE` `--export-cover FILE`
- `jzr audio trim` - Cut a time range out of audio files. `[SOURCES]...` `--start TIME` `--end TIME` `--duration TIME` `--exact`
### pdf ([reference/pdf.md](reference/pdf.md))
- `jzr pdf compress` - Make PDF files smaller, mostly by shrinking their images. `[SOURCES]...` `--level [lossless|recommended|strong]` `--strip`
- `jzr pdf from-images` - Make a PDF with one page per picture, in the order given. `PICTURES...` `--paper [a4|letter|original]` `--orientation [auto|portrait|landscape]` `--margin MM` `--smaller`
- `jzr pdf images` - Render the pages of a PDF as pictures. `FILE` `--to [jpg|png]` `--dpi N` `--quality N` `--pages PAGES`
- `jzr pdf info` - Show the pages, sizes, metadata and features of PDF files. `FILES...` `--json`
- `jzr pdf merge` - Join PDF files into one, in the order given. `[FILES]...`
- `jzr pdf organize` - Reorder, rotate or remove the pages of a PDF. `FILE` `--order PAGES` `--delete PAGES` `--rotate [PAGES:]ANGLE`
- `jzr pdf split` - Split a PDF into several: every page, every N pages, or groups. `FILE` `--every N` `--pages GROUPS`
- `jzr pdf text` - Print the text of a PDF, or convert it to Markdown. `FILE` `--markdown` `--pages PAGES` `--page-breaks`
### ffmpeg ([reference/ffmpeg.md](reference/ffmpeg.md))
- `jzr ffmpeg install` - Download ffmpeg and ffprobe into jzr's own folder. `--yes`
- `jzr ffmpeg remove` - Delete the ffmpeg that `jzr ffmpeg install` downloaded.
- `jzr ffmpeg status` - Show which ffmpeg and ffprobe jzr uses and where they come from.
### file ([reference/file.md](reference/file.md))
- `jzr file rename` - Rename many files at once; shows the plan unless --apply is given. `[SOURCES]...` `--hidden` `--replace OLD NEW` `--regex PATTERN REPL` `--ignore-case` `--case [lower|upper|title|slug|snake]` `--prefix TEXT` `--suffix TEXT` `--template TEXT` `--ext EXT` `--lower-ext` `--start N` `--sort [name|mtime|size]` `--reverse` `--apply` `--verbose` `--json`
### text ([reference/text.md](reference/text.md))
- `jzr text base64` - Encode text or a file as Base64, or decode it with -d. `[TEXT]` `--decode` `--file FILE` `--url-safe` `--copy`
- `jzr text diff` - Show how text file NEW differs from OLD, as a unified diff. `OLD` `NEW` `--unified N` `--ignore-space` `--strict-eol` `--color [auto|always|never]`
- `jzr text slug` - Convert text into a URL-safe slug, lowercase kebab-case by default. `[TEXT]` `--separator TEXT` `--preserve-case` `--include-date` `--copy`
- `jzr text url` - Percent-encode text for a URL, decode it with -d, or --parse a URL. `[TEXT]` `--decode` `--form` `--parse` `--json` `--copy`
### tools ([reference/tools.md](reference/tools.md))
- `jzr uuid` - Generate UUIDs (v4, or v7 with --v7). `--count N` `--v7` `--upper` `--no-hyphens` `--copy`
- `jzr password` - Generate passwords or passphrases. `--length N` `--no-upper` `--no-lower` `--no-digits` `--no-symbols` `--no-similar` `--words N` `--separator TEXT` `--count N` `--copy`
- `jzr hash` - Hash files or text (SHA-256 by default). `[FILES]...` `--text TEXT` `--algorithm [md5|sha1|sha256|sha384|sha512|sha3-256|sha3-512|blake2b]` `--expect HEX` `--check SUMFILE` `--json`
- `jzr timestamp` - Convert between Unix time and dates. `[VALUE]` `--unit [s|ms]` `--tz ZONE` `--utc` `--format STRFTIME` `--json`
- `jzr jwt` - Decode a JWT and check its times and HMAC signature. `[TOKEN]` `--secret-env NAME` `--secret-file FILE` `--require-valid` `--json`
### random ([reference/random.md](reference/random.md))
- `jzr random int` - Random whole numbers from MIN to MAX, both included (default 1 100). `[MIN MAX]` `--count N` `--unique`
- `jzr random pick` - Pick random ITEMS; without ITEMS, the lines of stdin. `[ITEMS]...` `--count N` `--repeat`
- `jzr random shuffle` - Print ITEMS in random order; without ITEMS, the lines of stdin. `[ITEMS]...`
### data ([reference/data.md](reference/data.md))
- `jzr data convert` - Convert data between JSON, YAML, CSV, TOML and Excel. `[FILE]` `--from [json|jsonl|yaml|csv|toml|xlsx]` `--to [json|jsonl|yaml|csv|toml|xlsx]` `--delimiter [,|;|tab||]` `--no-header` `--header-row N` `--types` `--nest` `--sheet NAME` `--indent N` `--minify`
- `jzr data json` - Pretty-print, minify or check JSON from FILES or stdin. `[FILES]...` `--indent N` `--tab` `--minify` `--sort-keys` `--check` `--write` `--python`
### env ([reference/env.md](reference/env.md))
- `jzr env compare` - Show the keys only in A (default .env.example) or only in B (default .env). `[A]` `[B]` `--values` `--json`
- `jzr env example` - Print the keys of FILE (default .env) as KEY= lines for a .env.example. `[FILE]` `--copy`
### port ([reference/port.md](reference/port.md))
- `jzr port kill` - Force-kill processes using a local port (Windows, Linux, and macOS). `PORT...` `--restart-hns`
- `jzr port show` - Show which processes are using local ports. `[PORT]...` `--udp` `--json`
### host ([reference/host.md](reference/host.md))
- `jzr host status` - Show host, CPU, memory, storage and GPU status. `--live` `--interval SECONDS` `--detail` `--json`
- `jzr host clean` - Find caches and other disposable data, and remove what you pick. `--yes` `--only ID[,ID...]` `--json`
- `jzr host procs` - Show what uses the most memory, grouped by application. `[PATTERN]` `--top N` `--each` `--kill` `--force` `--json`
### bind ([reference/bind.md](reference/bind.md))
- `jzr bind add` - Bind NAME to COMMAND, so that `jzr NAME` runs it. `NAME` `[COMMAND]...` `--force`
- `jzr bind list` - List the bindings. `--json`
- `jzr bind path` - Print where the bindings are stored.
- `jzr bind remove` - Remove bindings. `NAME...`
- `jzr bind run` - Run the binding NAME, with ARGS added to its command. `NAME` `[ARGS]...`
### completion ([reference/completion.md](reference/completion.md))
- `jzr completion bash` - Print the bash completion script.
- `jzr completion fish` - Print the fish completion script.
- `jzr completion install` - Set up completion for SHELL, or for the shell you are typing in. `[SHELL]`
- `jzr completion powershell` - Print the powershell completion script.
- `jzr completion remove` - Undo `jzr completion install` for SHELL, or for the current shell. `[SHELL]`
- `jzr completion zsh` - Print the zsh completion script.
<!-- commands:end -->

## Rules

- Sources stay as they are. Image, video and audio commands write a new file beside each source (`a.jpg`, `a_compressed.jpg`, `a_1600x1200.jpg`, `a_trim.mp4`) and print `source -> target` for each.
- An output that already exists is skipped. Pass `--overwrite` only when the user wants it replaced.
- `-o FILE` names the output of one source. `--outdir DIR` collects the outputs of many.
- PAGES are page numbers from 1 and ranges: `1-3,5`, `7-` (to the end), `2-last`; `last` is the last page, so there is no need to count pages first. `jzr pdf split --pages` separates its groups with `;`.
- With no SOURCES, the batch commands take every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters. Files named as SOURCES need no dry run. Preview a scan with `--dry-run` only when the folder may hold files the user did not mean.
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
