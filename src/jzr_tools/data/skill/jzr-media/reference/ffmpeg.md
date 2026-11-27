# jzr ffmpeg

## jzr ffmpeg install

```text
Usage: jzr ffmpeg install [OPTIONS]

  Download ffmpeg and ffprobe into jzr's own folder.

  The download is checked against the SHA-256 checksum its source publishes and the new
  copy is run once before it replaces an older one. Run it again to update.

Options:
  -y, --yes   Download without asking first.
```

Examples:

- `jzr ffmpeg install` - asks, then downloads ffmpeg and ffprobe

## jzr ffmpeg remove

```text
Usage: jzr ffmpeg remove [OPTIONS]

  Delete the ffmpeg that `jzr ffmpeg install` downloaded.
```

Examples:

- `jzr ffmpeg remove` - deletes the downloaded copy

## jzr ffmpeg status

```text
Usage: jzr ffmpeg status [OPTIONS]

  Show which ffmpeg and ffprobe jzr uses and where they come from.
```

Examples:

- `jzr ffmpeg status` - shows which ffmpeg is used
