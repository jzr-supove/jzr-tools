# jzr audio

## jzr audio convert

```text
Usage: jzr audio convert [OPTIONS] [SOURCES]...

  Convert audio files to another format.

  List SOURCES to convert those files, or leave them out to convert every audio file in
  --dir that is not already in the target format. The audio is copied when the target
  can hold it as it is, otherwise encoded.

Options:
  -t, --to TEXT        Target format: mp3, m4a, opus, ogg, flac or wav. Taken from
                       --output when left out.
  -f, --from TEXT      Only convert this source format; lets SOURCES omit the extension.
  -d, --dir DIRECTORY  Directory to scan when no SOURCES are listed.  [default: .]
  -p, --pattern TEXT   Regex filter applied to candidate filenames.
  -r, --recursive      Recurse into subdirectories while scanning.
  -o, --output FILE    Write the one listed SOURCE to this file.
  --outdir DIRECTORY   Write outputs here instead of beside each source.
  -b, --bitrate TEXT   Bitrate for lossy targets in kb/s, e.g. 128k; forces an encode.
  --overwrite          Replace existing output files.
  --dry-run            Show what would be converted; write nothing.
```

Examples:

- `jzr audio convert song.flac --to mp3 -b 320k` - writes song.mp3
- `jzr audio convert -d music -r --from wav --to flac` - converts every WAV in a folder tree

## jzr audio formats

```text
Usage: jzr audio formats [OPTIONS]

  Show the target formats, the ffmpeg in use and its audio encoders.
```

Examples:

- `jzr audio formats` - lists the target formats and encoders

## jzr audio info

```text
Usage: jzr audio info [OPTIONS] FILES...

  Show the format, duration, streams and tags of audio files.

  --json prints one array with the same facts and every tag.

Options:
  --json      Print one JSON array.
```

Examples:

- `jzr audio info song.mp3` - shows format, duration and tags
- `jzr audio info song.mp3 --json` - prints one JSON array with every tag

## jzr audio tags

```text
Usage: jzr audio tags [OPTIONS] FILES...

  Show or edit the tags, cover art and lyrics of audio files.

  With no options it prints the tags of each FILE. Works on MP3, M4A, FLAC, Ogg Vorbis
  and Opus, edits in place without touching the audio, and needs no ffmpeg. Keys: title,
  artist, album, albumartist, year, track, disc, genre, comment, composer. --clear runs
  first, so '--clear --set title=X' leaves only the title.

Options:
  --json               Print one JSON array of the tags.
  -s, --set KEY=VALUE  Set a tag; repeat for several.
  -u, --unset KEY      Remove a tag, or the lyrics; repeat for several.
  --clear              Remove every tag, the cover and the lyrics first.
  --cover IMAGE        Embed a JPEG or PNG as the front cover, replacing any picture.
  --no-cover           Remove the embedded pictures.
  --lyrics FILE        Embed the lyrics from a UTF-8 text file.
  --export-cover FILE  Write the embedded cover to FILE (.jpg or .png); changes nothing.
```

Examples:

- `jzr audio tags song.mp3` - prints the tags
- `jzr audio tags song.mp3 -s title="New name" -s year=2026` - sets two tags in the file
- `jzr audio tags song.mp3 --cover front.jpg` - embeds a front cover
- `jzr audio tags song.mp3 --export-cover cover.jpg` - saves the embedded cover

## jzr audio trim

```text
Usage: jzr audio trim [OPTIONS] [SOURCES]...

  Cut a time range out of audio files.

  WAV and FLAC are cut to the sample, losslessly. MP3, AAC, Opus and Vorbis are cut by
  copying, with no quality loss and to within one audio frame (about 1/40 s); --exact
  encodes them again at the source's bitrate for a cut to the millisecond. Tags and
  cover art are kept. The output is <name>_trim.<ext> in the source's format.

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
  --exact              Encode MP3, AAC, Opus or Vorbis again for a cut to the
                       millisecond.
  --overwrite          Replace existing output files.
  --dry-run            Show what would be converted; write nothing.
```

Examples:

- `jzr audio trim song.mp3 --start 0:10 --end 0:40` - cuts without encoding again
- `jzr audio trim talk.m4a --start 12.5 --duration 30 --exact` - cuts to the millisecond
