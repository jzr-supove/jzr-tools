---
name: jzr-media
description: Run the jzr command line for image, video and audio files. Convert, compress, resize, trim or inspect images, video and audio; make GIFs; edit audio tags. Use it instead of ffmpeg or Pillow scripts.
---

# jzr-media

Part of jzr, a command-line tool for everyday file and machine jobs. If `jzr` is not found, run it as `uvx --from jzr-tools jzr <command>` (needs uv), or ask the user to install it (`uv tool install jzr-tools`).

## Commands

Each line is a command with its own arguments and options, then what it does. Run `jzr <command> -h` only when an option's meaning is not clear; `reference/<group>.md` has worked examples.

### image
- `jzr image compress [SOURCES]... --to [jpg|png|webp|avif|heic] --quality N --size SIZE --max-side N --strip --background COLOR` - Make image files smaller, optionally to a target size.
- `jzr image convert [SOURCES]... --to TEXT --from TEXT --quality N --backend [auto|pillow|magick] --background COLOR` - Convert images to another format.
- `jzr image formats` - Show supported extensions and the conversion engines available.
- `jzr image info FILES... --json --no-gps` - Show the format, size, colour profile and metadata of images.
- `jzr image resize [SOURCES]... --width N --height N --percent P --max-side N --upscale --to TEXT --quality N --strip --background COLOR` - Make images smaller (or, with --upscale, larger).
- `jzr image strip [SOURCES]... --in-place --no-color-profile` - Remove metadata from JPEG, PNG and WebP files without re-encoding.
### video
- `jzr video compress [SOURCES]... --quality [high|balanced|small] --size SIZE --for [email|discord] --resize N --no-audio --strip` - Make video files smaller, optionally to a target size.
- `jzr video convert [SOURCES]... --to TEXT --from TEXT --codec [h264|hevc|av1|vp9] --quality [high|balanced|small] --crf N --resize N --fps N --no-audio --bitrate TEXT` - Convert video files to another container, or to audio.
- `jzr video formats` - Show the target formats, the ffmpeg in use and its encoders.
- `jzr video gif [SOURCES]... --start TIME --end TIME --duration TIME --width N --fps N` - Make animated GIFs from video files.
- `jzr video info FILES... --json` - Show the container, duration, size and streams of video files.
- `jzr video trim [SOURCES]... --start TIME --end TIME --duration TIME --fast` - Cut a time range out of video files.
### audio
- `jzr audio convert [SOURCES]... --to TEXT --from TEXT --bitrate TEXT` - Convert audio files to another format.
- `jzr audio formats` - Show the target formats, the ffmpeg in use and its audio encoders.
- `jzr audio info FILES... --json` - Show the format, duration, streams and tags of audio files.
- `jzr audio tags FILES... --json --set KEY=VALUE --unset KEY --clear --cover IMAGE --no-cover --lyrics FILE --export-cover FILE` - Show or edit the tags, cover art and lyrics of audio files.
- `jzr audio trim [SOURCES]... --start TIME --end TIME --duration TIME --exact` - Cut a time range out of audio files.
### ffmpeg
- `jzr ffmpeg install --yes` - Download ffmpeg and ffprobe into jzr's own folder.
- `jzr ffmpeg remove` - Delete the ffmpeg that `jzr ffmpeg install` downloaded.
- `jzr ffmpeg status` - Show which ffmpeg and ffprobe jzr uses and where they come from.

## Rules

- Sources stay as they are. Each command writes a new file beside its source (`a_compressed.jpg`, `a_1600x1200.jpg`, `a_trim.mp4`).
- Every result is printed as `source -> target (...)` with what was written, such as its size, dimensions or page count: read that line instead of checking the file again.
- An output that already exists is skipped. Pass `--overwrite` only when the user wants it replaced.
- `-o FILE` names the output of one source. `--outdir DIR` collects the outputs of many.
- With no SOURCES, a command takes every matching file in `--dir` (default: the current folder); `-r` adds subfolders and `-p REGEX` filters.
- Files named as SOURCES need no dry run. Preview a scan with `--dry-run` only when the folder may hold files the user did not mean.
- `jzr audio tags` with an editing option changes the file in place.
- Use `--json` where a command has it, to read results by program.
- Exit codes: 0 all went well, 1 something failed, 2 wrong usage. One bad file does not stop the others.
- Video commands and most audio commands need ffmpeg; `jzr ffmpeg status` says whether there is one. `jzr ffmpeg install` downloads it and `jzr ffmpeg remove` deletes it: suggest those to the user instead of running them.
- jzr does not crop, rotate, join, watermark or draw on images or video. Use another tool for those.
- Do not guess options: an unknown one exits 2.
