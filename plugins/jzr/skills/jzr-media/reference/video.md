# jzr video

## jzr video compress

```text
Usage: jzr video compress [OPTIONS] [SOURCES]...

  Make video files smaller, optionally to a target size.

  Each file is written as <name>_compressed.mp4, with H.264 and AAC. Without --size it
  is encoded at constant quality, at most 1080p, and a result that is not smaller than
  its source is not kept. With --size (or --for) it is encoded in two passes to come out
  at or under that size, at the highest resolution the size allows; a size that cannot
  work is refused before encoding, with the smallest size that would.

Options:
  -d, --dir DIRECTORY             Directory to scan when no SOURCES are listed.
                                  [default: .]
  -p, --pattern TEXT              Regex filter applied to candidate filenames.
  -r, --recursive                 Recurse into subdirectories while scanning.
  -o, --output FILE               Write the one listed SOURCE to this file.
  --outdir DIRECTORY              Write outputs here instead of beside each source.
  -q, --quality [high|balanced|small]
                                  Quality when no size is given.  [default: balanced]
  -s, --size SIZE                 Stay at or under this size, e.g. 25MB, 8M, 500KB (two
                                  passes).
  --for [email|discord]           A named size: email (18 MB) or discord (20 MB).
  --resize N                      Highest short edge in pixels (default 1080); never
                                  scales up.  [x>=2]
  --no-audio                      Leave the audio out.
  --strip                         Leave out the metadata (title, location, dates).
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be converted; write nothing.
```

Examples:

- `jzr video compress clip.mov` - writes clip_compressed.mp4
- `jzr video compress clip.mp4 -s 25MB` - stays at or under 25 MB
- `jzr video compress clip.mp4 --for discord --resize 720` - fits Discord, at most 720p

## jzr video convert

```text
Usage: jzr video convert [OPTIONS] [SOURCES]...

  Convert video files to another container, or to audio.

  List SOURCES to convert those files, or leave them out to convert every video file in
  --dir that is not already in the target container. Each stream is copied when the
  target can hold it, which is quick and loses nothing; otherwise it is encoded (H.264
  and AAC, or VP9 and Opus for webm). --codec, --resize and --fps make the video encode.
  --to gif makes the GIF 'jzr video gif' makes with its defaults.

Options:
  -t, --to TEXT                   Target format: mp4, mov, mkv or webm, mp3, m4a, opus,
                                  ogg, flac or wav for the sound only, or gif. Taken
                                  from --output when left out.
  -f, --from TEXT                 Only convert this source format; lets SOURCES omit the
                                  extension.
  -d, --dir DIRECTORY             Directory to scan when no SOURCES are listed.
                                  [default: .]
  -p, --pattern TEXT              Regex filter applied to candidate filenames.
  -r, --recursive                 Recurse into subdirectories while scanning.
  -o, --output FILE               Write the one listed SOURCE to this file.
  --outdir DIRECTORY              Write outputs here instead of beside each source.
  -c, --codec [h264|hevc|av1|vp9]
                                  Encode the video with this codec instead of copying
                                  it.
  -q, --quality [high|balanced|small]
                                  Quality of a video encode.  [default: balanced]
  --crf INTEGER RANGE             Constant-quality value for a video encode (lower is
                                  better).  [0<=x<=63]
  --resize N                      Scale the short edge down to N pixels; never up.
                                  [x>=2]
  --fps N                         Lower the frame rate to N.  [x>0]
  --no-audio                      Leave the audio out.
  -b, --bitrate TEXT              Bitrate for lossy audio targets in kb/s, e.g. 128k;
                                  forces an encode.
  --overwrite                     Replace existing output files.
  --dry-run                       Show what would be converted; write nothing.
```

Examples:

- `jzr video convert clip.mkv --to mp4` - copies the streams when MP4 can hold them
- `jzr video convert talk.mp4 --to mp3 -b 128k` - keeps only the sound
- `jzr video convert clip.mov -o clip.webm --resize 720` - encodes to WebM at 720p

## jzr video formats

```text
Usage: jzr video formats [OPTIONS]

  Show the target formats, the ffmpeg in use and its encoders.
```

Examples:

- `jzr video formats` - lists the target formats and encoders

## jzr video gif

```text
Usage: jzr video gif [OPTIONS] [SOURCES]...

  Make animated GIFs from video files.

  Each file becomes <name>.gif, of the whole video or of the part between --start and
  --end (or --duration). The GIF is at most --width pixels wide and --fps frames a
  second, its colours come from a palette made for the clip, and it loops forever. GIFs
  grow fast: one longer than 30 seconds gets a note with the size to expect.

  Times are seconds (90, 7.5) or [H:]MM:SS[.mmm] (1:30, 0:01:30.250).

Options:
  -d, --dir DIRECTORY  Directory to scan when no SOURCES are listed.  [default: .]
  -p, --pattern TEXT   Regex filter applied to candidate filenames.
  -r, --recursive      Recurse into subdirectories while scanning.
  -o, --output FILE    Write the one listed SOURCE to this file.
  --outdir DIRECTORY   Write outputs here instead of beside each source.
  --start TIME         Where the GIF starts.  [default: 0]
  --end TIME           Where the GIF ends.  [default: the end of the file]
  --duration TIME      How long the GIF lasts, instead of --end.
  --width N            Widest the GIF may be, in pixels; never scales up.  [default:
                       480; x>=2]
  --fps N              Highest frame rate; a slower video keeps its own.  [default: 15;
                       x>0]
  --overwrite          Replace existing output files.
  --dry-run            Show what would be converted; write nothing.
```

Examples:

- `jzr video gif clip.mp4 --start 5 --duration 3` - makes a three second GIF
- `jzr video gif clip.mp4 --width 320 --fps 10 -o small.gif` - makes a smaller GIF

## jzr video info

```text
Usage: jzr video info [OPTIONS] FILES...

  Show the container, duration, size and streams of video files.

  Rotation is given in degrees clockwise. --json prints one array with the same facts.

Options:
  --json      Print one JSON array.
```

Examples:

- `jzr video info clip.mp4` - shows duration, size and streams
- `jzr video info clip.mp4 --json` - prints one JSON array

## jzr video trim

```text
Usage: jzr video trim [OPTIONS] [SOURCES]...

  Cut a time range out of video files.

  The cut is exact by default: it starts on the asked frame and lasts the asked time,
  which means encoding the video again (at high quality, in the source's codec when
  there is an encoder for it). --fast copies the streams instead: instant and lossless,
  but the cut starts at the keyframe at or before --start, and the result line says
  where. The output is <name>_trim.<ext> in the source's container.

  Times are seconds (90, 7.5) or [H:]MM:SS[.mmm] (1:30, 0:01:30.250).

Options:
  -d, --dir DIRECTORY  Directory to scan when no SOURCES are listed.  [default: .]
  -p, --pattern TEXT   Regex filter applied to candidate filenames.
  -r, --recursive      Recurse into subdirectories while scanning.
  -o, --output FILE    Write the one listed SOURCE to this file.
  --outdir DIRECTORY   Write outputs here instead of beside each source.
  --start TIME         Where the cut starts.  [default: 0]
  --end TIME           Where the cut ends.  [default: the end of the file]
  --duration TIME      How long the cut lasts, instead of --end.
  --fast               Copy the streams: instant, but starts at the keyframe before
                       --start.
  --overwrite          Replace existing output files.
  --dry-run            Show what would be converted; write nothing.
```

Examples:

- `jzr video trim clip.mp4 --start 0:30 --end 1:15` - cuts exactly, encoding again
- `jzr video trim clip.mp4 --start 30 --duration 45 --fast` - copies, from the keyframe before 30 s
