"""Worked examples for each command, shown in the agent skill's references."""

from __future__ import annotations

# path of the command -> (command line, what it does)
EXAMPLES: dict[str, tuple[tuple[str, str], ...]] = {
    "image compress": (
        ("jzr image compress photo.jpg", "writes photo_compressed.jpg"),
        ("jzr image compress photo.jpg -s 300KB", "stays at or under 300 KB"),
        (
            "jzr image compress -d photos -r --to webp --max-side 1920 --outdir small",
            "compresses a folder tree into WebP, at most 1920 pixels a side",
        ),
    ),
    "image convert": (
        ("jzr image convert photo.heic --to jpg", "writes photo.jpg"),
        ("jzr image convert logo.png -o logo.webp -q 90", "takes the format from the output name"),
        ("jzr image convert -d scans --from tiff --to png --dry-run", "shows what a folder would become"),
    ),
    "image formats": (("jzr image formats", "lists the formats that can be read and written"),),
    "image info": (
        ("jzr image info photo.jpg", "shows size, colour profile, camera and GPS"),
        ("jzr image info a.png b.jpg --json", "prints one JSON array"),
    ),
    "image resize": (
        ("jzr image resize photo.jpg --width 1200", "keeps the proportions"),
        ("jzr image resize photo.jpg --width 800 --height 600", "fits inside an 800x600 box"),
        ("jzr image resize -d photos --max-side 1600 --outdir web", "resizes every image in a folder"),
    ),
    "video compress": (
        ("jzr video compress clip.mov", "writes clip_compressed.mp4"),
        ("jzr video compress clip.mp4 -s 25MB", "stays at or under 25 MB"),
        ("jzr video compress clip.mp4 --for discord --resize 720", "fits Discord, at most 720p"),
    ),
    "video convert": (
        ("jzr video convert clip.mkv --to mp4", "copies the streams when MP4 can hold them"),
        ("jzr video convert talk.mp4 --to mp3 -b 128k", "keeps only the sound"),
        ("jzr video convert clip.mov -o clip.webm --resize 720", "encodes to WebM at 720p"),
    ),
    "video formats": (("jzr video formats", "lists the target formats and encoders"),),
    "video gif": (
        ("jzr video gif clip.mp4 --start 5 --duration 3", "makes a three second GIF"),
        ("jzr video gif clip.mp4 --width 320 --fps 10 -o small.gif", "makes a smaller GIF"),
    ),
    "video info": (
        ("jzr video info clip.mp4", "shows duration, size and streams"),
        ("jzr video info clip.mp4 --json", "prints one JSON array"),
    ),
    "video trim": (
        ("jzr video trim clip.mp4 --start 0:30 --end 1:15", "cuts exactly, encoding again"),
        ("jzr video trim clip.mp4 --start 30 --duration 45 --fast", "copies, from the keyframe before 30 s"),
    ),
    "audio convert": (
        ("jzr audio convert song.flac --to mp3 -b 320k", "writes song.mp3"),
        ("jzr audio convert -d music -r --from wav --to flac", "converts every WAV in a folder tree"),
    ),
    "audio formats": (("jzr audio formats", "lists the target formats and encoders"),),
    "audio info": (
        ("jzr audio info song.mp3", "shows format, duration and tags"),
        ("jzr audio info song.mp3 --json", "prints one JSON array with every tag"),
    ),
    "audio tags": (
        ("jzr audio tags song.mp3", "prints the tags"),
        ('jzr audio tags song.mp3 -s title="New name" -s year=2026', "sets two tags in the file"),
        ("jzr audio tags song.mp3 --cover front.jpg", "embeds a front cover"),
        ("jzr audio tags song.mp3 --export-cover cover.jpg", "saves the embedded cover"),
    ),
    "audio trim": (
        ("jzr audio trim song.mp3 --start 0:10 --end 0:40", "cuts without encoding again"),
        ("jzr audio trim talk.m4a --start 12.5 --duration 30 --exact", "cuts to the millisecond"),
    ),
    "ffmpeg install": (("jzr ffmpeg install", "asks, then downloads ffmpeg and ffprobe"),),
    "ffmpeg remove": (("jzr ffmpeg remove", "deletes the downloaded copy"),),
    "ffmpeg status": (("jzr ffmpeg status", "shows which ffmpeg is used"),),
    "file rename": (
        ('jzr file rename -d photos --template "{date}-{n:03}"', "shows the plan, renames nothing"),
        ('jzr file rename -d photos --template "{date}-{n:03}" -y', "applies that plan"),
        ("jzr file rename draft-1.txt draft-2.txt --replace draft final -y", "replaces text in two names"),
        ("jzr file rename -d docs -r --case slug --lower-ext --json", "prints the plan as JSON"),
    ),
    "text base64": (
        ('jzr text base64 "hello"', "encodes text"),
        ("jzr text base64 -d aGVsbG8=", "decodes it"),
        ("jzr text base64 -f logo.png", "encodes a file"),
    ),
    "text diff": (
        ("jzr text diff old.txt new.txt", "exits 1 when they differ"),
        ("jzr text diff old.txt new.txt -U 0 -w", "leaves out context and indentation changes"),
    ),
    "text slug": (
        ('jzr text slug "Hello World"', "prints hello-world"),
        ('jzr text slug "Hello World" -s _ --date', "uses underscores, with the date in front"),
    ),
    "text url": (
        ('jzr text url "a b&c"', "prints a%20b%26c"),
        ('jzr text url --parse "https://example.com/p?x=1" --json', "prints the parts as JSON"),
    ),
    "uuid": (
        ("jzr uuid", "prints one random UUID"),
        ("jzr uuid --v7 -n 5", "prints five that sort by time"),
    ),
    "password": (
        ("jzr password -l 24", "prints a 24 character password"),
        ("jzr password -w 5", "prints a five word passphrase"),
    ),
    "random int": (("jzr random int 1 6 -n 3", "rolls three dice"),),
    "random pick": (("jzr random pick red green blue", "picks one"),),
    "random shuffle": (("jzr random shuffle a b c", "prints them in random order"),),
    "hash": (
        ("jzr hash setup.exe", "prints the SHA-256"),
        ("jzr hash setup.exe -a md5 --expect d41d8cd98f00b204e9800998ecf8427e", "says OK or MISMATCH"),
        ("jzr hash -c SHA256SUMS", "checks every listed file"),
        ('jzr hash -t "hello" --json', "hashes text and prints JSON"),
    ),
    "timestamp": (
        ("jzr timestamp", "shows the time now"),
        ("jzr timestamp 1763715600 --tz Europe/Berlin", "shows a Unix time in a zone"),
        ("jzr timestamp 2026-11-21T09:40 --utc --json", "converts a date to Unix time"),
    ),
    "data json": (
        ("jzr data json config.json", "pretty-prints to stdout"),
        ("jzr data json --check a.json b.json", "only checks the files"),
        ("jzr data json config.json --sort-keys -w", "rewrites the file in place"),
    ),
    "data convert": (
        ("jzr data convert config.yaml --to json", "prints the YAML as JSON"),
        ("jzr data convert users.json -o users.csv", "nested keys become dotted columns"),
        ("jzr data convert report.xlsx --to json --sheet Sales", "reads one sheet"),
        ("jzr data convert rows.csv --types --to xlsx", "numbers stay numbers; writes rows.xlsx"),
    ),
    "env compare": (
        ("jzr env compare", "compares .env.example with .env, keys only"),
        ("jzr env compare .env.example .env.test --json", "compares two named files"),
    ),
    "env example": (
        ("jzr env example", "prints the keys of .env without values"),
        ("jzr env example .env -o .env.example", "writes the file"),
    ),
    "jwt": (
        ("jzr jwt - --json", "decodes a token from stdin"),
        ("jzr jwt - --secret-env JWT_SECRET --require-valid", "checks the signature and times"),
    ),
    "port kill": (("jzr port kill 3000 8080", "ends the processes on both ports"),),
    "port show": (
        ("jzr port show", "lists every listening TCP port"),
        ("jzr port show 3000 --json", "shows who uses port 3000"),
    ),
    "host status": (
        ("jzr host status", "shows CPU, memory, storage and GPU"),
        ("jzr host status --json", "prints everything as JSON"),
    ),
    "host clean": (
        ("jzr host clean --dry-run", "shows what could be removed"),
        ("jzr host clean --json", "prints what was found, removes nothing"),
    ),
    "host procs": (
        ("jzr host procs -n 5", "shows the five biggest applications"),
        ("jzr host procs chrome --each --json", "lists matching processes as JSON"),
    ),
    "bind add": (
        ("jzr bind add serve python -m http.server 8000", "makes jzr serve run that command"),
        ('jzr bind add show "git show {1} --stat"', "puts the first argument in place of {1}"),
    ),
    "bind list": (("jzr bind list --json", "prints the bindings as JSON"),),
    "bind path": (("jzr bind path", "prints where they are stored"),),
    "bind remove": (("jzr bind remove serve", "removes one binding"),),
    "bind run": (("jzr bind run serve", "runs it, even when a command has the same name"),),
    "completion bash": (("jzr completion bash", "prints the script"),),
    "completion fish": (("jzr completion fish", "prints the script"),),
    "completion install": (("jzr completion install --dry-run", "shows what would change"),),
    "completion powershell": (("jzr completion powershell", "prints the script"),),
    "completion remove": (("jzr completion remove --dry-run", "shows what would change"),),
    "completion zsh": (("jzr completion zsh", "prints the script"),),
}
