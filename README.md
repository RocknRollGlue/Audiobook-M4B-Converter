# Audiobook M4B Converter

Convert a folder of MP3 files into a single chaptered `.m4b` audiobook with title, author, narrator, description and cover art.

## Requirements

- Python 3.8+ (standard library only, no `pip install` needed)
- ffmpeg and ffprobe. If they are missing, the script tries to install them automatically using winget/Chocolatey/Scoop (Windows), Homebrew (macOS) or apt/dnf/pacman/zypper (Linux). Use `--no-install` to disable this.

## Usage

```
python convert_audiobook.py input_dir [-o OUTPUT] [-a {copy|BITRATE}] [-y] [--ffmpeg PATH] [--no-install] [-h]
```

Example:

```
python convert_audiobook.py "testFiles\Moving Pictures"
python convert_audiobook.py "testFiles\Moving Pictures" -a 128k -o "C:\Books\Moving Pictures.m4b" -y
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `input_dir` | yes | Folder containing the MP3 files (sorted naturally: `Part 2` before `Part 10`). |
| `-o, --output OUTPUT` | no | Output `.m4b` path. Default: `<input_dir>\<title>.m4b`. |
| `-a, --audio {copy\|BITRATE}` | no | Default `copy`. `copy` keeps the original MP3 audio untouched (lossless), but some players (e.g. Apple Books) may not support MP3 inside M4B. A bitrate such as `128k` or `192k` re-encodes to AAC for broader player support (lossy; the source is already lossy, so use a higher bitrate than the source). |
| `-y, --overwrite` | no | Overwrite the output file if it exists. |
| `--ffmpeg PATH` | no | Path to the ffmpeg binary or its folder. Default: search PATH and common install locations. |
| `--no-install` | no | Don't try to install ffmpeg if missing. Default: install automatically. |
| `-h, --help` | no | Show help. |

## Input layout

Metadata is optional:

```
book/
    Part 001.mp3
    Part 002.mp3
    ...
    metadata/
        metadata.json
        cover.jpg
```

- `metadata.json` (title, `creator[]` with `author`/`narrator` roles, `description.full`, `chapters[]` with `title`/`spine`/`offset`) is used for tags and chapters.
- Cover: `metadata/cover.jpg|jpeg|png` (also `folder`/`front`, or the same names in the book folder).
- Without metadata, the title, artist and comment come from the first MP3's tags, and the title falls back to the folder name.

## metadata.json example

Only the fields below are used; any others (e.g. the top-level `spine` array of durations, or `coverUrl`) are ignored. Note that `chapters[].spine` is different and is used.

```json
{
  "title": "Title of the book",
  "description": {
    "full": "<p>HTML or plain text description.</p>"
  },
  "creator": [
    { "name": "Author", "role": "author" },
    { "name": "narrator", "role": "narrator" }
  ],
  "chapters": [
    { "title": "Opening Credits", "spine": 0, "offset": 0 },
    { "title": "Chapter 1", "spine": 0, "offset": 95.5 },
    { "title": "Chapter 2", "spine": 1, "offset": 12.0 }
  ]
}
```

- `title`: book title (also used as the output file name).
- `description.full`: HTML is stripped to plain text.
- `creator[]`: `role` of `author` goes to artist, `narrator` goes to composer. Multiple names are joined with commas.
- `chapters[]`: `spine` is the 0-based index of the MP3 file (in sorted order) and `offset` is the start time in seconds within that file.

## Chapters

Chapters come from `metadata.json`. If it defines fewer than two chapters, one chapter per MP3 file is created ("Part 1", "Part 2", ...).
