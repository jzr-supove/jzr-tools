# Video and audio

[← jzr-tools](../README.md) · [Install](install.md) · [Images](images.md) · [Video and audio](video-audio.md) · [PDF](pdf.md) · [Text and data](text-and-data.md) · [Files](files.md) · [Ports and host](system.md) · [Shortcuts](shortcuts.md) · [Completion](completion.md) · [AI agents](agents.md)

## Setting up ffmpeg

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

## Info and formats

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

## Video conversion

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

## Video compression

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

## Audio conversion

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

## Audio tags

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

## Trimming

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

## GIFs

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
