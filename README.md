# jzr-tools

`jzr-tools` puts fast everyday jobs behind one command, `jzr`: convert images,
video and audio, trim and compress media, make GIFs, edit audio tags, free busy
ports and clean up your machine. It runs on Windows, Linux and macOS.

## Install

No Python needed. The installer sets up [uv](https://docs.astral.sh/uv/),
which downloads a suitable Python by itself, and then installs the `jzr`
command.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.ps1 | iex"
```

macOS and Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/jzr-supove/jzr-tools/main/install.sh | sh
```

Open a new terminal afterwards and run `jzr --help`. Run the same line again
to upgrade.

If you already have uv or pipx:

```bash
uv tool install jzr-tools      # upgrade: uv tool upgrade jzr-tools
pipx install jzr-tools         # needs Python 3.11 or newer; upgrade: pipx upgrade jzr-tools
```

To try it without installing anything: `uvx --from jzr-tools jzr --help`.

The video and audio commands use ffmpeg. If it is not on your `PATH`, run
`jzr ffmpeg install` once to download it into jzr's own folder.

## Install from source

For global usage with `pipx`, from a clone of the repository:

```bash
pipx install .
jzr --help
```

For development with `uv`:

```bash
uv sync
uv run jzr --help
uv run python -m unittest discover -s tests
```

You can also run the package directly:

```bash
python -m jzr_tools --help
```

## Layout

- `src/jzr_tools/cli.py`: root Click group and the command registry. Commands
  are imported only when invoked, so each `jzr` call loads just what it needs;
  a new command is added by listing it in `_COMMANDS`
- `src/jzr_tools/commands/`: CLI-facing command modules
- `src/jzr_tools/core/`: plain Python business logic
- `tests/`: CLI and core behavior checks

## Commands

Commands follow one shape, `jzr <thing> <action>`, grouped by what they work
on. Run `jzr -h`, or `-h` after any group or command, for the details.

| Command | What it does |
|---------|--------------|
| `jzr image convert` | Convert images between formats, one file or a whole directory |
| `jzr image <from>2<to>` | Shorthand for a format pair, e.g. `jzr image png2jpg` |
| `jzr image formats` | List supported extensions and the conversion engines available |
| `jzr video convert` | Convert video to mp4, mov, mkv or webm, take the sound out as audio, or make a GIF |
| `jzr video <from>2<to>` | Shorthand for a format pair, e.g. `jzr video mov2mp4` |
| `jzr video compress` | Make videos smaller as H.264 MP4, at a quality or under a size (`--size 25MB`) |
| `jzr video info` | Show the container, duration, size and streams of video files |
| `jzr video formats` | List video targets, the ffmpeg in use and which encoders it has |
| `jzr video trim` | Cut a time range out of video files, exactly or instantly from a keyframe |
| `jzr video gif` | Make a looping animated GIF of a video, or of part of it |
| `jzr audio convert` | Convert audio files to mp3, m4a, opus, ogg, flac or wav |
| `jzr audio <from>2<to>` | Shorthand for a format pair, e.g. `jzr audio wav2mp3` |
| `jzr audio info` | Show the format, streams and tags of audio files |
| `jzr audio formats` | List audio targets, the ffmpeg in use and which encoders it has |
| `jzr audio trim` | Cut a time range out of audio files without losing quality |
| `jzr audio tags` | Show or edit tags, cover art and lyrics of MP3, M4A, FLAC, Ogg and Opus files |
| `jzr ffmpeg install` | Download ffmpeg and ffprobe into jzr's own folder |
| `jzr ffmpeg status` | Show which ffmpeg and ffprobe jzr uses and where they come from |
| `jzr ffmpeg remove` | Delete the ffmpeg that `jzr ffmpeg install` downloaded |
| `jzr text slug` | Turn text into a URL-safe kebab-case slug, optionally dated and copied |
| `jzr text base64` | Encode text or a file as Base64, or decode it |
| `jzr text url` | Percent-encode or decode text for URLs, or show the parts of a URL |
| `jzr file rename` | Rename many files at once: replace text, change case, number them; previews first |
| `jzr uuid` | Generate UUIDs, version 4 or time-ordered version 7 |
| `jzr password` | Generate strong passwords or word passphrases |
| `jzr random` | Random numbers (`int`), picks (`pick`) or a shuffled list (`shuffle`) |
| `jzr hash` | Hash files or text, or check a `SHA256SUMS` list |
| `jzr timestamp` | Turn Unix time into dates and dates into Unix time, in any time zone |
| `jzr data json` | Pretty-print, minify, check or rewrite JSON, changing only whitespace |
| `jzr jwt` | Show a JWT's header, payload and times, and check an HMAC signature |
| `jzr port show` | List listening ports and their processes, or show what uses given ports |
| `jzr port kill` | Force-kill the processes using one or more local ports |
| `jzr killport` | Shortcut for `jzr port kill` |
| `jzr host status` | Show host, CPU, memory, storage and GPU status |
| `jzr host clean` | Find caches and other disposable data, and remove what you pick |
| `jzr host procs` | Show what uses the most memory; optionally end what you pick |
| `jzr bind add` | Save a long command under a short name, run as `jzr <name>` |
| `jzr bind list` | List your shortcuts |
| `jzr bind remove` | Remove shortcuts |
| `jzr completion` | Set up tab completion for bash, zsh, fish or PowerShell, or print its script |
| `jzr version` | Print the installed toolkit version |

## Image Conversion

`jzr image` converts with Pillow, which is installed with the toolkit, so it
needs nothing else on your machine. HEIC and AVIF are included. Pass
`--backend magick` to use ImageMagick from `PATH` instead.

Name the files, or leave them out to convert a directory:

```bash
jzr image convert photo.png --to jpg -q 90          # one file
jzr image convert photo.png -o small/photo.webp     # one file, named output
jzr image convert a.png b.webp --to avif            # several files
jzr image convert --to jpg                          # every image in this directory
jzr image convert --from heic --to jpg -d ~/Photos -r
jzr image convert --to webp --pattern "^IMG_" -q 85 # only names matching a regex
```

When the source format is fixed, the pair shorthand is shorter. It is the same
command as `image convert --from <from> --to <to>`:

```bash
jzr image png2jpg photo.png -q 90
jzr image png2jpg photo                # the extension may be left off
jzr image heic2jpg -d ~/Photos -r      # every .heic under a directory
```

Options: `-t/--to`, `-f/--from`, `-q/--quality` (1-100), `-d/--dir`,
`-p/--pattern`, `-r/--recursive`, `-o/--output`, `--outdir`, `-b/--backend` (`auto`,
`pillow`, `magick`), `--overwrite`, `--dry-run`.

Behavior worth knowing:

- Without `--from`, a directory scan picks up every supported image that is
  not already in the target format.
- With a known source format, a listed file with a different extension is
  rejected rather than converted.
- `--dir` and `--recursive` only apply when no files are listed; combining
  them with listed files is an error.
- `--pattern` is a regular expression matched against each candidate's path
  relative to the search directory (just the file name unless `--recursive`
  is used), or against the file name of each listed file.
- `-o/--output FILE` names the output of a single listed file; its extension
  sets the target format, so `--to` can be left out. For several files use
  `--outdir`.
- Outputs land beside their source unless `--outdir` is given. When a
  directory is scanned with `--recursive`, its subdirectories are recreated
  under `--outdir`; explicitly listed sources are written flat.
- No output is written twice in one run. Sources that would share a name
  (`logo.png` and `logo.webp` both becoming `logo.jpg`) get `-2`, `-3`, ...
  appended, in sorted source order.
- Existing outputs are skipped unless `--overwrite` is passed.
- An animated source (GIF, WebP) stays animated when the target is `gif`,
  `webp` or `avif`, with its frame timings. Any other target gets the first
  frame.
- Transparent areas become white in formats that cannot hold transparency
  (`jpg`, `bmp`, `ppm`).
- Photos are written upright: a rotation stored in EXIF is applied to the
  pixels. Other EXIF data and the colour profile are kept where the target
  format can store them.
- `-q/--quality` is 1-100, higher is better, and applies to the lossy formats
  (`jpg`, `webp`, `avif`, `heic`). Without it they are written at 90. The
  other formats are lossless; passing `-q` for them prints a note on stderr
  and converts anyway.
- An output is written under a temporary name and renamed when it is complete,
  so a failed conversion never leaves a broken file behind.
- A failed file does not stop the batch; the run exits `1` and reports which
  files failed.
- `--dry-run` lists what would be converted and writes nothing.

With `--backend magick`:

- `--dry-run` prints the exact ImageMagick command for each file.
- For `png` and `tiff`, `-q` tunes compression rather than being ignored.
- An output whose path contains `%` is written under a temporary
  `jzr-tmp-...` name and then renamed, because ImageMagick would read the `%`
  as a numbered-sequence escape; `--dry-run` prints a note saying so.
- Some ImageMagick builds cannot write every format (HEIC on Windows, for
  one). Such a conversion is reported as failed instead of leaving a file in
  another format under the requested name.

`jzr image formats` lists the supported extensions, the Pillow versions in
use, and whether ImageMagick was found on `PATH`.

## Video and Audio

`jzr video` and `jzr audio` use ffmpeg and ffprobe, except `jzr audio tags`,
which needs neither. jzr does not ship them, but it can fetch them for you:

```bash
jzr ffmpeg install        # asks first; -y/--yes skips the question
jzr ffmpeg status         # which ffmpeg is used, its version, where it came from
jzr ffmpeg remove         # delete jzr's copy
```

`jzr ffmpeg install` says what it will download, from where, how big it is
and where it goes, then puts ffmpeg and ffprobe in jzr's own folder:
`%LOCALAPPDATA%\jzr\ffmpeg` on Windows, `~/Library/Application Support/jzr/ffmpeg`
on macOS, `$XDG_DATA_HOME/jzr/ffmpeg` (default `~/.local/share/jzr/ffmpeg`)
elsewhere. Set `JZR_DATA_DIR` to use another folder. It needs no admin rights
and changes nothing else on the system, PATH included. Run it again to update.

- Windows and Linux (x64, arm64) get the `gpl-shared` build of the newest
  ffmpeg release from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds);
  macOS (Apple silicon, Intel) gets the release build from
  [ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de). Only ffmpeg,
  ffprobe and the libraries they load are kept (about 180 MB on Windows,
  from an 86 MB download).
- Each download is checked against the SHA-256 checksum its source
  publishes next to it. That catches a broken or cut-off download; it does
  not protect against the source itself being compromised, since the
  checksum comes from the same place.
- The new copy is run once before it replaces the old one. A failed
  download, a checksum mismatch or an archive that tries to write outside
  its folder installs nothing and leaves the previous copy as it was.
- jzr goes to the network only inside `jzr ffmpeg install` (or when you say
  yes to the offer below): no update checks, nothing sent besides the
  download requests.

ffmpeg and ffprobe are looked for in this order: `JZR_FFMPEG` and
`JZR_FFPROBE` (paths to the programs), then jzr's own copy, then `PATH`.
When a command finds none and you are at a terminal, it offers to run the
install and then carries on. In a script or pipe it asks nothing, prints
`ffmpeg was not found. Run: jzr ffmpeg install` and exits with code 1.

On a machine with no known build (32-bit, BSD), install ffmpeg yourself and
it is found on `PATH`:

```bash
winget install Gyan.FFmpeg     # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian, Ubuntu
```

`info` describes files: container, duration, size, overall bitrate, the
video stream (codec, resolution, frame rate, pixel format), each audio
stream, and rotation (in degrees clockwise) and HDR transfer when present.
`audio info` adds title, artist, album, year, track and whether there is
cover art. `--json` prints one array with the same facts as numbers and
every tag. A file that is not media is reported on its own line and the rest
are still shown; the exit code is then 1.

```bash
jzr video info clip.mov
jzr video info *.mp4 --json
jzr audio info song.mp3
```

`jzr video formats` and `jzr audio formats` list the target formats, which
ffmpeg and ffprobe are used, their versions and where they were found, and
which of the encoders jzr uses this ffmpeg build has. Include their output
when reporting a problem.

### Video conversion

`jzr video convert` turns video files into `mp4`, `mov`, `mkv` or `webm`,
takes their sound out as `mp3`, `m4a`, `opus`, `ogg`, `flac` or `wav`, or
makes a `gif`. It picks its sources and outputs the way `jzr audio convert`
does:

```bash
jzr video convert clip.mov --to mp4                # one file
jzr video convert clip.mov -o out/clip.webm        # named output; --to not needed
jzr video convert *.mkv --to mp4 --outdir mp4
jzr video convert --to mp4 -d ~/Videos -r          # every video file under a directory
jzr video convert clip.mp4 --to mp4 --codec h264 -o plays-everywhere.mp4
jzr video convert clip.mov --to mp4 --resize 720 --fps 30
jzr video mov2mp4 clip                             # pair shorthand; extension optional
jzr video mp42mp3 talk.mp4                         # the sound only
jzr video convert clip.mp4 --to gif                # the GIF `jzr video gif` makes by default
```

Options: `-t/--to`, `-f/--from`, `-c/--codec` (`h264`, `hevc`, `av1`,
`vp9`), `-q/--quality` (`high`, `balanced`, `small`; default `balanced`),
`--crf N`, `--resize N`, `--fps N`, `--no-audio`, `-b/--bitrate` (audio
targets), `-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`,
`--outdir`, `--overwrite`, `--dry-run`.

- Each stream is copied when the target container can hold its codec, which
  is quick and loses nothing (`clip.mov -> clip.mp4 (video copied, audio
  copied)`). What does not fit is encoded to the container's default: H.264
  and AAC for `mp4`, `mov` and `mkv`, VP9 and Opus for `webm`.
- `--codec` always encodes the video with that codec; the audio is still
  copied when it fits. A codec the container cannot hold is refused
  (`webm` takes `vp9` and `av1`, `mov` takes `h264` and `hevc`).
- `--quality` and `--crf` set the encoder's constant quality (lower CRF is
  better and bigger). They only matter when the video is encoded; when it is
  copied, the result line says so.
- `--resize N` scales the short edge down to N pixels (never up, even
  sizes), and `--fps N` lowers the frame rate; both make the video encode,
  unless the source is already that small or slow.
- Encoded H.264 is 8-bit 4:2:0 so that every player opens it; HEVC in
  `mp4`/`mov` is tagged `hvc1` for Apple players. `mp4` and `mov` outputs
  have their index at the front, so they start playing before they are
  fully downloaded. An HDR source encoded to H.264 is not tone-mapped and
  looks washed out; the result line warns about it.
- Rotation is kept: a copied stream keeps its rotation flag, an encoded one
  is turned upright.
- The best video stream and the audio stream with the most channels are
  used. Other audio tracks, subtitles and cover art are left out, and the
  result line names them. Tags are kept.
- An audio target works like `jzr audio convert` on the video's sound: AAC
  goes into `.m4a` untouched, and `--bitrate` applies. The video options are
  refused with it, and a video with no audio track fails with
  `no audio track`.
- `--to gif` (and pairs such as `mp42gif`) makes the same GIF as
  `jzr video gif` with its defaults: the whole video, at most 480 pixels
  wide and 15 frames a second. The other options are refused with it;
  `jzr video gif` is the command with the GIF options.
- Scanning a directory picks video files (`mp4 m4v mov mkv webm avi wmv flv
  mpg mpeg ts mts m2ts 3gp ogv`) that are not already in the target
  container.
- A file that cannot be read, or that needs an encoder this ffmpeg build
  lacks (`this ffmpeg build has no libx265 encoder`), fails on its own; the
  rest still convert and the exit code is 1. `jzr video formats` shows
  which encoders the build has.
- `--dry-run`, the temporary `jzr-tmp-...` output and the progress line work
  as in `jzr audio convert`.

### Video compression

`jzr video compress` makes video files smaller. The output is always MP4
with H.264 and AAC, so it plays everywhere, and is named
`<name>_compressed.mp4`:

```bash
jzr video compress clip.mov                        # constant quality, at most 1080p
jzr video compress clip.mov -q small --resize 720
jzr video compress clip.mov --size 25MB            # at or under 25 MB
jzr video compress clip.mov --for discord -o share.mp4
jzr video compress -d ~/Videos -r --for email --outdir small
```

Options: `-q/--quality` (`high`, `balanced`, `small`; default `balanced`),
`-s/--size SIZE`, `--for email|discord`, `--resize N`, `--no-audio`,
`--strip`, `-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`,
`--outdir`, `--overwrite`, `--dry-run`.

- Without a size, the video is encoded at constant quality, with the short
  edge capped at 1080 pixels (`--resize N` sets another cap; never scaled
  up). A result that is not smaller than its source is thrown away:
  `skip clip.mp4: already smaller than the result`.
- `--size` takes decimal sizes (`25MB`, `8M`, `500KB`, `1.5GB`). The video is
  encoded in two passes at the bitrate that fills the size, less 4% for the
  container. The highest resolution of the source (at most 1080p), 720p,
  480p and 360p that still has enough bits for each pixel is used, and the
  audio is 128 kb/s, or 96 kb/s when the size is tight.
- `--for email` is 18 MB (mail adds about a third for encoding, so this fits
  a 25 MB limit) and `--for discord` is 20 MB, Discord's limit for free
  accounts.
- A size that cannot work is refused before anything is encoded, with the
  smallest that would:
  `too small for a 12:04 video; the smallest is about 42 MB (or use --no-audio)`.
  A source already at or under the size is skipped.
- If the encoder still overshoots, the file is encoded once more at a lower
  bitrate; if that is still too big, the file fails. A result over the size
  is never kept, and an existing output is only replaced by a result that
  fits.
- AAC audio that is already stereo and no larger than the planned bitrate
  is copied; other audio is encoded, and surround is mixed down to stereo.
- Tags such as the title, date and `location` are kept; `--strip` leaves
  every tag out. Apple's `com.apple.quicktime.*` keys (the device, and the
  location in iPhone clips) are not written to MP4 by ffmpeg, so they are
  gone either way. The video is turned upright, so a rotated phone clip
  needs no rotation flag.
- A directory scan picks every video file except earlier
  `..._compressed` results. Sources, outputs, `--dry-run` (both passes are
  shown), the progress line and failures work as in `jzr video convert`.

### Audio conversion

`jzr audio convert` turns audio files into `mp3`, `m4a`, `opus`, `ogg`,
`flac` or `wav`. It picks its sources and outputs the way `jzr image convert`
does:

```bash
jzr audio convert song.wav --to mp3               # one file
jzr audio convert song.wav -o out/song.mp3        # named output; --to not needed
jzr audio convert *.flac --to m4a --outdir m4a
jzr audio convert --to mp3 -d ~/Music -r          # every audio file under a directory
jzr audio wav2mp3 song                            # pair shorthand; extension optional
```

Options: `-t/--to`, `-f/--from`, `-b/--bitrate` (kb/s, e.g. `128k`),
`-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`, `--outdir`,
`--overwrite`, `--dry-run`.

- When the target can hold the source's codec, the audio is copied, with no
  quality loss and almost instantly (AAC from an `.mp4` or `.m4a` into
  `.m4a`, MP3 into `.mp3` under `--outdir`). Otherwise it is encoded. The
  result line says which: `song.wav -> song.mp3 (audio encoded to mp3)`.
- Encoding defaults: MP3 VBR about 190 kb/s, AAC 192 kb/s, Opus 128 kb/s,
  Vorbis quality 5, FLAC level 5, WAV PCM. FLAC and WAV keep 24-bit sources
  at 24 bits and write 16 bits otherwise. `--bitrate` sets the rate of the
  lossy targets and forces an encode; for `flac` and `wav` it prints a note
  and is ignored.
- Tags are carried over. Cover art is carried into `mp3`, `m4a` and `flac`;
  the other formats get none, and the result line says it was dropped.
- Scanning a directory picks audio files only (`mp3 m4a aac opus ogg oga
  flac wav aif aiff wma mka`) that are not already in the target format.
  A listed video file is accepted too, and its first audio track is
  converted; `jzr video` is the place for video work.
- A file with no audio track, or one ffmpeg cannot read, fails on its own;
  the rest still convert and the exit code is 1.
- `--dry-run` prints the ffmpeg command for each file and writes nothing.
- Output is written under a temporary `jzr-tmp-...` name and renamed when
  ffmpeg succeeds, so a failure or Ctrl+C leaves no partial file and never
  damages an existing one. On a terminal a progress line is shown on stderr.

### Audio tags

`jzr audio tags` shows and edits the tags of MP3, M4A, FLAC, Ogg Vorbis and
Opus files in place. It reads and writes the tags with
[mutagen](https://github.com/quodlibet/mutagen), so it needs no ffmpeg and
never touches the audio itself.

```bash
jzr audio tags song.mp3                                  # print the tags
jzr audio tags *.flac --json                             # one JSON array
jzr audio tags song.mp3 --set "title=Song" -s artist=Band -s track=3/12
jzr audio tags *.mp3 -s album=Record -s year=2020        # same change to every file
jzr audio tags song.m4a --unset comment
jzr audio tags song.opus --clear --set title=Song        # only the title is left
jzr audio tags song.mp3 --cover front.jpg --lyrics words.txt
jzr audio tags song.flac --no-cover
jzr audio tags song.mp3 --export-cover front.jpg
```

- Keys: `title`, `artist`, `album`, `albumartist`, `year`, `track`, `disc`,
  `genre`, `comment`, `composer`. `year` is `YYYY`, `YYYY-MM` or
  `YYYY-MM-DD`; `track` and `disc` are `N` or `N/M`.
- Printing shows the format, each tag that is set, the cover (type and size)
  and how many lines of lyrics there are. A tag with several values is shown
  joined with `; `.
- `-s/--set KEY=VALUE` and `-u/--unset KEY` repeat; `--unset lyrics` removes
  the lyrics. `--clear` removes every tag (including ones outside the list
  above), the cover and the lyrics, and runs before the other changes.
- `--cover IMAGE` embeds a JPEG or PNG (told apart by content, not by name)
  as the front cover and replaces any picture already there; `--no-cover`
  removes the pictures. `--lyrics FILE` embeds the lyrics from a UTF-8 text
  file. `--export-cover FILE` writes the embedded cover out; the name must
  end in `.jpg`/`.jpeg` or `.png` to match the picture, and an existing file
  is not replaced.
- The format is read from the file's content, so a file with the wrong
  extension is still handled as what it is. MP3 tags are written as ID3v2.3,
  which Windows Explorer and older players read best.
- Edits are written to a temporary `jzr-tmp-...` copy next to the file,
  which then replaces it, so a failed write leaves the file as it was. A
  read-only file is refused.
- Unknown keys, bad values and options that contradict each other are usage
  errors (exit code 2) and change nothing. A file that cannot be read or
  written fails on its own; the rest are still handled and the exit code
  is 1.

### Trimming

`jzr video trim` and `jzr audio trim` keep a time range of a file and write
it beside the source as `<name>_trim.<ext>`, in the same container or format:

```bash
jzr video trim clip.mp4 --start 1:05 --end 1:30          # exact: starts on the asked frame
jzr video trim clip.mp4 --start 1:05 --duration 25 --fast  # instant copy from the keyframe
jzr video trim clip.mov --end 10 -o first-ten-seconds.mov
jzr video trim *.mp4 --start 3 --outdir trimmed           # same cut of every file
jzr audio trim song.flac --start 0:30 --end 2:10.500
jzr audio trim talk.mp3 --start 90 --exact                 # re-encode for a cut to the millisecond
```

Options: `--start TIME` (default 0), `--end TIME` or `--duration TIME`
(default: to the end of the file), `--fast` (video) or `--exact` (audio),
`-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`, `--outdir`,
`--overwrite`, `--dry-run`. Times are seconds (`90`, `7.5`) or
`[H:]MM:SS[.mmm]` (`1:30`, `0:01:30.250`).

- A video cut is exact by default: it starts on the asked frame and lasts the
  asked time. The video is encoded again at high quality in the source's
  codec (H.264, HEVC, AV1 or VP9), or in H.264 (VP9 for `webm`) when there is
  no encoder for it; the audio is copied. This takes as long as an encode of
  the cut part.
- `--fast` copies the streams instead: instant and lossless, but a cut can
  only start on a keyframe, so it starts at the keyframe at or before
  `--start` and the result line says where:
  `clip.mp4 -> clip_trim.mp4 (video copied, audio copied; starts at 0:05.000,
  the keyframe before 0:07.000)`. The end is where you asked, give or take a
  frame or two, since copied video can only be cut between whole packets.
  `.ogv` (Theora) can only be cut with `--fast`.
- WAV and FLAC are cut to the sample and stay lossless. MP3, AAC, Opus and
  Vorbis are cut by copying, with no quality loss and to within one audio
  frame (about 1/40 s); `--exact` encodes them again at the source's bitrate
  for a cut to the millisecond.
- Tags are kept, and so is cover art (in MP3, M4A, FLAC, Ogg and Opus).
  The best video stream and the audio stream with the most channels are
  kept in a video; other audio tracks and subtitles are left out, and the
  result line names them.
- `--output` must keep the source's extension; use `jzr video convert` or
  `jzr audio convert` to change the format. `--start`, `--end` and
  `--duration` that contradict each other are usage errors (exit code 2). A
  start past the end of a file fails that file and names its length; an end
  past it cuts to the end, and the result line says so.
- Scanning a directory picks video (or audio) files and leaves out earlier
  `_trim` results. An audio file given to `jzr video trim`, or a video given
  to `jzr audio trim`, fails on its own, as does any file ffmpeg cannot
  read; the rest are still cut and the exit code is 1.
- `--dry-run`, the temporary `jzr-tmp-...` output and the progress line work
  as in `jzr audio convert`.

### GIFs

`jzr video gif` makes an animated GIF of a video, or of a part of it, and
writes it beside the source as `<name>.gif`:

```bash
jzr video gif clip.mp4                                   # the whole clip, 480 px wide, 15 fps
jzr video gif clip.mp4 --start 1:05 --duration 4         # four seconds from 1:05
jzr video gif clip.mov --end 3 --width 320 --fps 10 -o reaction.gif
jzr video gif *.mp4 --outdir gifs
```

Options: `--start TIME` (default 0), `--end TIME` or `--duration TIME`
(default: to the end of the file), `--width N` (default 480), `--fps N`
(default 15), `-d/--dir`, `-p/--pattern`, `-r/--recursive`, `-o/--output`,
`--outdir`, `--overwrite`, `--dry-run`. Times are written as for
`jzr video trim`.

- `--width` is the most the GIF may be wide; a narrower video keeps its size,
  and the height follows the picture's shape. `--fps` likewise only lowers
  the frame rate. A rotated phone video comes out upright.
- The colours come from a palette made for the clip in the same ffmpeg run,
  so no palette file is written. The GIF loops forever and has no sound.
- GIFs grow fast. There is no length limit, but a GIF longer than 30 seconds
  gets a note with the range of sizes to expect (`long GIF (0:45): expect a
  few to tens of MB; ...`); a shorter part, a smaller `--width` or a lower
  `--fps` makes it smaller.
- The range works as in `jzr video trim`: contradicting times are usage
  errors (exit code 2), a start past the end of a file fails that file, and
  an end past it stops at the end. A file without a video track (an audio
  file) fails on its own, as does any file ffmpeg cannot read; the rest are
  still made and the exit code is 1.
- `--dry-run`, the temporary `jzr-tmp-...` output and the progress line work
  as in `jzr audio convert`.

## Ports

See what is using a port, then free it:

```bash
jzr port show              # every listening TCP port and its process
jzr port show --udp        # ...and bound UDP ports too
jzr port show 3000 8080    # every socket on these ports
jzr port show --json       # for scripts
jzr port kill 3000
jzr killport 3000 8080     # shortcut for `jzr port kill`, several ports at once
```

Without a port, `jzr port show` prints a table sorted by port. A process that
listens on the same port over IPv4 and IPv6 gets one row:

```text
PORT  PROTO  PID    NAME         ADDRESS
135   TCP    1700   svchost.exe  0.0.0.0, [::]
445   TCP    4      System       0.0.0.0, [::]
3000  TCP    18472  node.exe     127.0.0.1
```

With ports, it prints one line per socket with the PID, name, protocol, local
address and state. `--json` prints one array of sockets in both cases, each
with `port`, `protocol`, `address`, `pid` (`null` when unknown), `name` and
`status` (`null` for UDP). `port show` changes nothing.

`jzr port kill` force-kills all visible processes bound to the exact local port, for TCP and UDP
over IPv4 and IPv6. Works on Windows, Linux, and macOS using `psutil`, installed
with the toolkit. On Unix this sends SIGKILL; on Windows it uses TerminateProcess.
Remote connections to the port are not targeted, and unrelated child processes
are not killed. There is no confirmation prompt.

Ports must be between 1 and 65535. Give several ports to free them in one call;
a process holding more than one of them is killed once and reported for each.
The command prints each killed PID and returns success if no process or Windows
port exclusion is found; a failure on one port does not stop the others. Permission errors or processes that do not exit
produce a nonzero exit code, while other matching processes are still attempted.
Processes that restart automatically may reclaim the port.

Use an Administrator terminal on Windows or `sudo jzr killport 3000` on Unix
when permissions prevent inspection or termination. macOS requires root for
system-wide socket inspection; Linux may omit sockets without sufficient
permissions (see the [psutil documentation](https://psutil.readthedocs.io/stable/#psutil.net_connections)).

### Windows excluded ports (EACCES)

Windows can reserve a port even when no process is listening. `jzr killport`
checks active TCP/UDP exclusion ranges for IPv4 and IPv6, prints matching ranges,
and returns a nonzero exit code when the port is excluded. This explains cases
such as Next.js reporting `listen EACCES: permission denied 0.0.0.0:3000` while
no port owner is visible.

Choose an unreserved port, or try your HNS recovery workflow from an
**Administrator terminal**:

```powershell
jzr killport 3000 --restart-hns
```

The flag stops and starts the Host Network Service only when an exclusion is
detected. This can interrupt Docker, containers, and WSL networking. The command
rechecks all exclusion ranges and tests local binds afterward; it fails if the
port is still unavailable. With several excluded ports, HNS is restarted once. A restart is not guaranteed to clear a reservation,
and Windows may reserve the port again later. Administered exclusions (`*`) are
reported without restarting HNS. The command does not delete reservations or
change dynamic port ranges. Linux and macOS reject `--restart-hns`.

## Host status

See what the machine is and how busy it is:

```bash
jzr host status                 # host, CPU, memory, storage, GPU
jzr host status --detail        # plus per-core usage, swap, every mount, network, sensors
jzr host status --live          # keep refreshing until Ctrl+C
jzr host status --live -n 0.5   # refresh every half second
jzr host status --json          # everything, for scripts
```

```text
╭─ fedora ──────────────────────────────────────────────────────╮
│  Fedora Linux 42 (Workstation Edition)  x86_64  up 4h 30m     │
├─ CPU ─────────────────────────────────────────────────────────┤
│  12th Gen Intel(R) Core(TM) i7-12700  (12 cores, 20 threads)  │
│  Usage      ████░░░░░░░░░░░░░░░░   19%                        │
│  Load       1.94  2.13  2.32                                  │
├─ Memory ──────────────────────────────────────────────────────┤
│  RAM        ██████████████░░░░░░   70%     21.8 / 31.1 GiB    │
├─ Storage ─────────────────────────────────────────────────────┤
│  /          ████████████████░░░░   81%   363.5 / 451.6 GiB    │
│  /boot      ██████████░░░░░░░░░░   48%   430.6 / 973.4 MiB    │
├─ GPU ─────────────────────────────────────────────────────────┤
│  NVIDIA GeForce RTX 4080                                      │
│  Usage      ██████░░░░░░░░░░░░░░   31%                        │
│  Memory     █████░░░░░░░░░░░░░░░   25%      4.0 / 16.0 GiB    │
│  Temp       63°C                                              │
│                                                               │
│  Intel UHD Graphics 770                                       │
╰───────────────────────────────────────────────────────────────╯
```

Works on Windows, Linux and macOS using `psutil`, installed with the toolkit.
Anything a system does not expose is left out of the text and is `null` in
JSON; the command still exits `0`.

Options: `-l/--live`, `-n/--interval SECONDS` (default 2, minimum 0.2, only
with `--live`), `-d/--detail`, `--json`.

Behavior worth knowing:

- CPU usage is measured over a quarter of a second for a single reading, and
  over the whole interval for each `--live` refresh.
- Memory "used" is total minus available, so cache the system can give back
  does not count as used.
- Storage lists each device once, at its shortest mount point; `--detail`
  lists every mount with its device, filesystem and free space. Loop devices
  and snap images are never listed.
- GPUs: NVIDIA cards are read through `nvidia-smi` on every system (usage,
  memory, temperature, and in `--detail` driver and power). On Linux, AMD
  cards report the same through the kernel, and Intel cards are listed by
  name. On Windows and macOS other adapters are listed by name only, because
  neither system offers a simple way to read their usage.
- Load average is not shown on Windows, which has none. Temperatures come
  from the kernel's sensors and are only available on Linux.
- `--json` always carries everything, with or without `--detail`. Sizes are
  bytes, percentages run 0-100, and times are ISO 8601. With `--live` it
  prints one compact JSON document per line.
- `--live` redraws in place on a terminal and leaves the last reading on
  screen when you stop it. Piped to a file, readings are appended one after
  another.
- Colour is used only on a terminal, and never when `NO_COLOR` is set.
- The frame is drawn with box characters. Where the output cannot show them
  (a redirected stream on Windows, an ASCII locale) it falls back to `+`, `-`,
  `|` and `[####....]` bars.
- On a terminal the report never exceeds the window: lines that do not fit
  are cut with `…`, and `--live` also stops at the window's height. Piped
  output is never cut.

Linux and Windows 11 (with an NVIDIA card) have been run on real hardware. The
macOS code path, AMD GPU readings and battery reporting are covered by tests
on recorded sample output only.

## Host clean

Find what can be deleted to get disk space back, tick what should go, confirm:

```bash
jzr host clean                    # scan, pick from a checkbox list, confirm, remove
jzr host clean --dry-run          # only look: what was found and what would be done
jzr host clean --yes              # no questions: remove everything that is safe to remove
jzr host clean --only uv,pip      # just these items (asks once; add --yes to skip that)
jzr host clean --json             # the scan, for scripts; removes nothing
```

```text
? What should be removed? (Use arrow keys to move, <space> to select, <a> to toggle, <i> to invert)
   Safe to remove
 » ● uv cache                         24.1 GiB
   ● Docker build cache               19.9 GiB
   ● pip cache                        15.6 GiB
   Review first
   ○ Docker images no container uses  35.3 GiB
   ○ Trash                             4.2 GiB
  Description: Packages are downloaded and built again when needed. Runs: uv cache clean
? Remove 3 items, about 59.6 GiB? (y/N)
```

What it looks for on Linux:

| Tier | Items | Ticked by default |
|------|-------|-------------------|
| Safe to remove | Docker build cache; uv, pip, npm, Cargo, Yarn, pnpm, Go build, node-gyp, Mesa shader and thumbnail caches | yes |
| Review first | Trash; stopped Docker containers; Docker images no container uses; any other folder of 100 MiB or more in `~/.cache` (listed as `cache:<name>`) | no |
| Needs root | systemd journal beyond the newest 500 MiB; the dnf or apt package cache | no |

And on Windows, mostly under `%LOCALAPPDATA%`:

| Tier | Items | Ticked by default |
|------|-------|-------------------|
| Safe to remove | Docker build cache; uv, pip, npm, Cargo, Yarn, pnpm, Go build and node-gyp caches; the graphics drivers' shader caches (NVIDIA, AMD, Intel, Direct3D) | yes |
| Review first | Temp files (`%TEMP%`, `SquirrelTemp`); crash dumps; the Recycle Bin; Docker containers and images; other large folders in `~/.cache` | no |
| Needs administrator | `C:\Windows\Temp`; Windows Update downloads | no |

The ids for `--only` are the same everywhere (`jzr host clean --only x` with an
id the other system has prints `x: nothing to remove`). `GOCACHE`,
`YARN_CACHE_FOLDER`, `UV_CACHE_DIR`, `PIP_CACHE_DIR`, `npm_config_cache` and
`CARGO_HOME` move a cache on either system.

Behavior worth knowing:

- Nothing is removed before you confirm, and `--dry-run` and `--json` never
  remove anything. `--yes` on its own removes the safe tier only; anything
  else has to be ticked or named with `--only`.
- It only looks in known places. It does not hunt for "temporary-looking"
  files, and it never offers Docker volumes, project folders or `/tmp`.
- Where a tool has its own clean-up command it is used (`uv cache clean`,
  `npm cache clean --force`, `docker builder prune --all --force`,
  `journalctl --vacuum-size=500M`, `dnf clean all`); otherwise the cache
  folder is emptied and kept. `--dry-run` shows which.
- Root items run through `sudo`, which asks for your password itself. With
  `--yes` they run as `sudo -n` and fail instead of asking.
- On Windows, administrator items are offered in an elevated terminal, or
  when Windows sudo is turned on; then jzr runs itself through `sudo` for
  them and measures again afterwards. With `--yes` and no elevation they fail
  instead of raising a prompt nobody answers. `C:\Windows\Temp` cannot be
  read without elevation, so it shows up only in an elevated terminal.
- On Windows, files a program still has open cannot be deleted; they are left
  in place and counted (`Temp files: removed 8.0 GiB; 31 files in use were
  kept`), and the item still counts as cleaned. Read-only files are removed.
- The Recycle Bin is sized and emptied through the Windows shell, on every
  drive at once.
- Sizes are estimates. The uv cache shares files with your environments, so
  less may come back than shown; the last line reports the free space before
  and after.
- Symbolic links (and Windows junctions) are never followed, and a link
  inside `~/.cache` is never offered as a cache.
- On Windows a hard-linked file counts once per link: Windows does not report
  link counts while listing a folder, and asking per file made sizing the uv
  cache ten times slower.
- A scan of a large cache takes a while (about 25 seconds here for 68 GiB).
- Without a terminal it refuses to guess: pass `--dry-run`, `--json` or
  `--yes`.
- Exit code `1` means something could not be removed (the rest still was) or
  the list was cancelled.

Removal has been run against throwaway folders only, on Linux and Windows;
the Docker, `sudo`, journal, dnf and apt commands, the Recycle Bin and
Windows sudo are covered by tests with them faked and have not been run for
real. The Windows scan has been run on a real machine.

## Host procs

See what is using memory, by application, and optionally end some of it:

```bash
jzr host procs                  # the 15 biggest applications
jzr host procs chrome           # only rows whose name or command line contains "chrome"
jzr host procs --each -n 30     # single processes, with PID and command line
jzr host procs --kill           # tick rows, confirm, and end them
jzr host procs --kill --force   # ...and force-kill whatever ignores the request
jzr host procs --json           # for scripts
```

```text
╭─ Memory ───────────────────────────────────────────────╮
│  RAM   ██████████████░░░░░░   72%   22.3 / 31.1 GiB    │
│  Swap  ████████████████████  100%     8.0 / 8.0 GiB    │
├─ Applications (5 of 166) ──────────────────────────────┤
│  Application         Procs    Memory       Swap  User  │
│  qemu-system-x86_64      1   3.4 GiB   36.1 MiB  sam   │
│  pycharm                 1   2.9 GiB  263.3 MiB  sam   │
│  msedge                 29   2.4 GiB  577.4 MiB  sam   │
│  node                   40   2.0 GiB    2.0 GiB  sam   │
│  claude                  4   1.1 GiB   86.9 MiB  sam   │
├─ Total ────────────────────────────────────────────────┤
│  Top 5                  75  11.8 GiB    2.9 GiB        │
│  All 166               354  17.1 GiB    6.7 GiB        │
│  Outside processes           5.2 GiB    1.3 GiB        │
│  In use                     22.3 GiB    8.0 GiB        │
╰────────────────────────────────────────────────────────╯
```

Behavior worth knowing:

- A row is every process of one user running the same program, so a browser
  is one line. All scripts run by the same interpreter also share a line; use
  `--each` to tell them apart.
- Memory and swap are each process's fair share of what it uses (pages shared
  by several processes are split between them), so the numbers add up. For
  other users' processes only the plain resident size is readable.
- The `Total` section adds up the Procs, Memory and Swap columns. `Top N` is
  the rows in the table; `All M` is everything that matched, including what
  `--top` left out (only `All M` appears when nothing was left out).
- `Outside processes` is what the machine has in use that belongs to no
  process: the kernel's own memory, shared memory, and cache it does not
  count as free. `All` plus `Outside processes` gives `In use`, the same
  figures as the bars at the top. Both rows are left out when you filter with
  a pattern, because the sums then cover only what matched.
- The report uses the same frame as `jzr host status`: plain `+ - |`
  characters where the output cannot show boxes, and cut to the window's
  width on a terminal. A protected row ends with the reason, such as
  `(system)` or `(this terminal)`.
- `--kill` ticks nothing for you. Processes are first asked to exit; after 5
  seconds it asks whether to force-kill what is left (`--force` skips that
  question). Only the processes listed are signalled, not their children.
- It will not end this terminal (the `jzr` process and its parents), other
  users' processes, or the session itself: `systemd`, the desktop shell, the
  display server, the sound and message services, `sshd`. A group that
  contains one of these can still be ticked; the protected ones are kept.
- `--kill` needs a terminal. For scripted killing use `pkill`.
- Exit code `1` means a process could not be ended or is still running.

On Windows:

- "Asked to exit" means each chosen program's windows are told to close, as
  clicking their X would, so it can save or ask you first. A program without
  a window cannot be asked, so it goes straight to the force-kill question.
  Force-killing is `TerminateProcess`.
- Windows keeps no per-process share of the page file, so the table has no
  Swap column and `--json` gives `null` for it. The Swap bar at the top is
  the page file.
- Protected as `(system)`: services and anything owned by `NT AUTHORITY`, and
  the shell and log-on processes (`explorer.exe`, `dwm.exe`, `csrss.exe`,
  `winlogon.exe`, `sihost.exe`, `svchost.exe` and the like).

Tried on Linux and Windows 11; ending processes only on throwaway child
processes.

## Bind

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

### Arguments in the middle

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

### Other commands and details

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

## Text

Turn text into a lowercase kebab-case slug:

```bash
jzr text slug "ApiSix deployment Notes"         # api-six-deployment-notes
jzr text slug "ApiSix deployment Notes" --date  # 2026-11-15_api-six-deployment-notes
jzr text slug "ApiSix deployment Notes" -c      # ...and copy it to the clipboard
jzr text slug "myVarName" -s _                  # my_var_name
jzr text slug "Read Me" --preserve-case         # Read-Me
```

Omit the text argument to paste or pipe multiline content. Runs of spaces,
newlines, and punctuation collapse into one separator, and camelCase or
PascalCase words are split (`HTTPServer` becomes `http-server`). The date
prefix is always followed by `_`. `jzr text kebab` is the same command under
its old name.

The result is ASCII. Accented Latin letters lose their accents, Russian and
Uzbek Cyrillic are transliterated (`Привет мир` becomes `privet-mir`), and
letters such as `ß` and `ø` are spelled out. Other scripts are dropped; if
nothing is left, the command exits `1` instead of printing an empty result.

### Base64

```bash
jzr text base64 "hello"                 # aGVsbG8=
jzr text base64 -d aGVsbG8=             # hello
jzr text base64 -f logo.png             # a file's bytes
jzr text base64 -d -o logo.png < logo.b64
jzr text base64 --url-safe "a?b"        # YT9i, the - and _ alphabet, no padding
```

Without text or `--file`, it reads stdin, and when encoding it uses the bytes
exactly: `echo hi | jzr text base64` gives `aGkK`, the newline included, as
`base64` does (PowerShell pipes end lines with `\r\n`, giving `aGkNCg==`;
pass the text as an argument to leave the line break out). Decoding ignores spaces and line breaks but refuses anything
else that is off (a wrong character, wrong padding, a length no data has)
rather than guessing. Decoded bytes that are not text are not printed; write
them with `-o FILE` (`--overwrite` to replace a file). `-c` also copies the
result.

### URLs

```bash
jzr text url "a b&c=d"                  # a%20b%26c%3Dd
jzr text url -d "a%20b%26c%3Dd"         # a b&c=d
jzr text url --form "a b"               # a+b, as in query strings and forms
jzr text url --parse "https://bob:secret@example.com:8443/a?q=1&q=2#top"
```

Encoding works like JavaScript's `encodeURIComponent`. A `%` that is not
followed by two hex digits, or bytes that are not UTF-8, is an error on
decoding, not replaced. Piped input loses one trailing line break, so
`echo x | jzr text url` gives `x`.

`--parse` prints the scheme, host, port, user, path, fragment and each query
parameter on its own line, in order, with duplicates and empty names kept
and values decoded. A password in the URL shows as `***`. `--json` prints the
same for scripts.

## Timestamps

```bash
jzr timestamp                           # now
jzr timestamp 1795236000                # Unix seconds
jzr timestamp 1795236000000             # milliseconds, told apart by size
jzr timestamp 2026-11-21T09:40          # a local date and time
jzr timestamp 2026-11-21T09:40 --tz Europe/Berlin
jzr timestamp 1795236000 --format "%d %B %Y"
```

```
Unix seconds  1795236000
Unix ms       1795236000000
UTC           2026-11-21T04:40:00Z
Local         2026-11-21T09:40:00+05:00 (Asia/Tashkent)
Relative      2 hours ago
```

- A number of at least 100,000,000,000 is read as milliseconds, anything
  smaller as seconds; `--unit s` or `--unit ms` decides instead. Fractions
  are kept (`1.125` is 1125 ms), and negative numbers work as they are
  (`jzr timestamp -86400`).
- Dates are ISO 8601: `2026-11-21`, `2026-11-21T09:40`, with seconds,
  fractions, `Z` or an offset such as `+05:00`. Without an offset the time is
  local, or in the `--tz` zone. A local time that the clocks skip or show
  twice when they change is an error that says so, instead of being moved.
- `--tz` takes IANA names (`Europe/Berlin`, `America/New_York`); `--utc` is
  `--tz UTC`. On Windows the local zone is shown with the name Windows gives
  it, such as `West Asia Standard Time`.
- `--format` uses Python's `strftime`. Platform extras such as `%-d` work on
  Linux and macOS but not on Windows.
- `--json` prints `seconds`, `milliseconds`, `utc`, `local` and `zone`.

## JSON

```bash
jzr data json response.json              # pretty-print with 2 spaces
curl -s https://example.com/api | jzr data json
jzr data json --minify --sort-keys a.json
jzr data json --check *.json             # exit 1 and say where, if any is invalid
jzr data json --write config.json        # rewrite the file in place
python -c "print({'a': True})" | jzr data json --python
```

- Only whitespace changes. Numbers stay as written (`1.10`, `1e400` and
  long integers are not rounded), string escapes such as `é` are not
  decoded, and duplicate keys are kept in their order.
- `--indent N` (0 to 8, default 2), `--tab` or `--minify` set the layout;
  `--sort-keys` sorts every object by key.
- An error names the input, line, column and what was expected, for example
  `bad.json: invalid JSON at line 2 column 7: expected a value`. With several
  files the others still run; the exit code is 1 if any failed.
- `--write` prints `FILE: formatted` or `FILE: already formatted` and leaves
  an already formatted file untouched. The file keeps its line endings, its
  byte order mark and whether it ends with a newline.
- `--python` reads a Python literal, such as a printed dict with `True`,
  `None` and single quotes, without running it, and prints it as JSON.
- Output always ends lines with LF, on Windows too.

## JWTs

```bash
jzr jwt eyJhbGciOiJIUzI1NiIs...
jzr jwt "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
jzr jwt --secret-env JWT_SECRET eyJhbGciOiJIUzI1NiIs...
jzr jwt --require-valid --json < token.txt
```

```
Header
{
  "alg": "HS256",
  "typ": "JWT"
}

Payload
{
  "sub": "123",
  "exp": 1795348800
}

exp  2026-11-22T12:00:00Z (in 1 day)   not expired
iat  not present
nbf  not present
```

- Decoding only shows what the token says; it proves nothing. Without a
  secret, jzr says on stderr that the signature was not checked.
- To check an HS256, HS384 or HS512 signature, give the secret with
  `--secret-env NAME` or `--secret-file FILE` (one final line break is
  dropped), never on the command line. jzr then prints `signature valid`
  or `signature INVALID`; an invalid signature exits 1. Tokens signed with
  `none`, RSA or EC keys, or with `crit` header parameters, are not checked.
- `exp` counts as expired from the second it names. `--require-valid` exits
  1 when the token is expired, not yet valid (`nbf`) or has an invalid
  signature, and says which.
- `--json` prints the header, payload, each time claim and the signature
  result. The token and the secret never appear in error messages.

## Renaming files

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

## Generators

```bash
jzr uuid                       # 5cdd86e0-af4b-4c87-9cee-5ea81baaa2b5
jzr uuid --v7 -n 3             # time-ordered: they sort in the order made
jzr password                   # 20 characters: letters, digits and symbols
jzr password -l 12 --no-symbols --no-similar
jzr password -w 6              # bobcat-rotten-distrust-chamber-spore-cinch
jzr password -c                # copy to the clipboard without printing it
jzr random int                 # 1 to 100
jzr random int -10 10 -n 5 --unique
jzr random pick tea coffee water
jzr random shuffle < names.txt
```

`jzr uuid` makes version 4 UUIDs; `--v7` makes version 7 ones, which start
with the time, so a batch made in one run sorts in creation order.
`--upper`, `--no-hyphens`, `-n` and `-c` (copy as well) change the output.

`jzr password` always uses at least one character of each type it is given
(`--no-upper`, `--no-lower`, `--no-digits`, `--no-symbols` take one out;
`--no-similar` leaves out `I l 1 O 0 o`). `-w N` makes a passphrase of N words
(3-20) from the EFF Long Wordlist, joined by `-` (`--separator`). On a
terminal it also says how strong the result is (`20 characters, about 129
bits`). `-c` puts it on the clipboard instead of the screen, so it does not
stay in your terminal's history.

`jzr random int [MIN MAX]` includes both ends; `--unique` never repeats a
number, even across a huge range. `jzr random pick` and `jzr random shuffle`
take items as arguments, or one per line on stdin; `pick -n 3` picks three
different ones, `--repeat` allows repeats.

Every random value comes from the operating system's secure source
(Python's `secrets`), with no bias towards any value.

## Hashes

```bash
jzr hash file.iso                          # sha256, printed like sha256sum
jzr hash *.zip > SHA256SUMS                # a list sha256sum -c can read
jzr hash -c SHA256SUMS                     # file.zip: OK / FAILED / missing
jzr hash file.iso --expect 9c2c9d42...     # OK or MISMATCH (exit 1)
jzr hash -t "some text" -a md5             # hash text instead of a file
```

`-a` chooses `md5`, `sha1`, `sha256` (default), `sha384`, `sha512`,
`sha3-256`, `sha3-512` or `blake2b`. Without files, jzr hashes stdin. The
lines end in LF on every system, so a list written on Windows works with
`sha256sum -c` too. `--check` finds the files next to the list, whatever
folder you run it from, and takes the algorithm from the digest length (or
`-a`). `--json` prints the results for scripts.

## Shell completion

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

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](https://github.com/jzr-supove/jzr-tools/blob/main/CONTRIBUTING.md)
for how a tool is laid out, how to add one and how to run the tests.

## License and credit

`jzr-tools` is created by JZR SUPOVE and released under the
[Apache License 2.0](https://github.com/jzr-supove/jzr-tools/blob/main/LICENSE).
You may use, change and redistribute it, including in commercial products.
When you redistribute it or a work based on it, keep the
[NOTICE](https://github.com/jzr-supove/jzr-tools/blob/main/NOTICE) file with
its credit line, as section 4 of the license asks.

`jzr password -w` uses the [EFF Long Wordlist](https://www.eff.org/dice) by
Joseph Bonneau for the Electronic Frontier Foundation, under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The words are
unchanged; only the dice numbers were removed. EFF does not endorse jzr.
