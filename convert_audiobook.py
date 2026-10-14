#!/usr/bin/env python3
"""Convert a folder of MP3 files (plus optional metadata) into a chaptered M4B audiobook.

Expected folder layout (metadata is optional):
    book/
        Part 001.mp3, Part 002.mp3, ...
        metadata/metadata.json   (title, creator[], description, spine[], chapters[])
        metadata/cover.jpg

Requires ffmpeg and ffprobe; if missing, the script tries to install them
(winget/choco/scoop, brew, or apt/dnf/pacman/zypper) unless --no-install is given.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def find_tool(name, hint=None):
    if hint:
        candidate = os.path.join(hint, name) if os.path.isdir(hint) else hint
        if shutil.which(candidate):
            return shutil.which(candidate)
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Microsoft", "WinGet", "Links", name + ".exe"),
        os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", name + ".exe"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def install_ffmpeg():
    """Try to install ffmpeg with the platform's package manager. Returns True if a command succeeded."""
    sudo = [] if sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0 else ["sudo"]
    if sys.platform == "win32":
        candidates = [("winget", ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                                  "--accept-source-agreements", "--accept-package-agreements"]),
                      ("choco", ["choco", "install", "ffmpeg", "-y"]),
                      ("scoop", ["scoop", "install", "ffmpeg"])]
    elif sys.platform == "darwin":
        candidates = [("brew", ["brew", "install", "ffmpeg"])]
    else:
        candidates = [("apt-get", sudo + ["apt-get", "install", "-y", "ffmpeg"]),
                      ("dnf", sudo + ["dnf", "install", "-y", "ffmpeg"]),
                      ("pacman", sudo + ["pacman", "-S", "--noconfirm", "ffmpeg"]),
                      ("zypper", sudo + ["zypper", "install", "-y", "ffmpeg"])]
    for tool, cmd in candidates:
        if shutil.which(tool):
            print("ffmpeg not found; installing: " + " ".join(cmd))
            if subprocess.run(cmd).returncode == 0:
                return True
    return False


def locate_ffmpeg(hint, allow_install):
    ffmpeg = find_tool("ffmpeg", hint)
    ffprobe = find_tool("ffprobe", os.path.dirname(ffmpeg) if ffmpeg else None)
    if (not ffmpeg or not ffprobe) and allow_install and install_ffmpeg():
        ffmpeg = find_tool("ffmpeg", hint)
        ffprobe = find_tool("ffprobe", os.path.dirname(ffmpeg) if ffmpeg else None)
    if not ffmpeg or not ffprobe:
        sys.exit("error: ffmpeg/ffprobe not found and could not be installed automatically. "
                 "Install ffmpeg manually (https://ffmpeg.org/download.html) or use --ffmpeg.")
    return ffmpeg, ffprobe


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def run_probe(ffprobe, path):
    out = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    return json.loads(out.stdout)


def strip_html(text):
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def escape_meta(value):
    value = str(value).replace("\\", "\\\\")
    for ch in "=;#":
        value = value.replace(ch, "\\" + ch)
    return value.replace("\r", "").replace("\n", "\\\n")


def fmt_time(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def load_metadata(folder):
    path = os.path.join(folder, "metadata", "metadata.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_cover(folder):
    for d in (os.path.join(folder, "metadata"), folder):
        for ext in ("jpg", "jpeg", "png"):
            for name in ("cover", "folder", "front"):
                p = os.path.join(d, f"{name}.{ext}")
                if os.path.isfile(p):
                    return p
    return None


def build_chapters(meta, files, durations):
    starts = [0.0]
    for d in durations:
        starts.append(starts[-1] + d)
    total = starts[-1]

    points = []
    for ch in meta.get("chapters", []):
        idx = ch.get("spine", 0)
        if 0 <= idx < len(files):
            points.append((starts[idx] + float(ch.get("offset", 0)), ch.get("title") or f"Chapter {len(points) + 1}"))
    points.sort()

    if len(points) < 2 and len(files) > 1:
        points = [(starts[i], f"Part {i + 1}") for i in range(len(files))]
    elif not points:
        points = [(0.0, "Chapter 1")]
    if points[0][0] > 0:
        points[0] = (0.0, points[0][1])

    chapters = []
    for i, (start, title) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else total
        chapters.append((start, end, title))
    return chapters


def audio_setting(value):
    if value.lower() == "copy":
        return "copy"
    if re.fullmatch(r"\d+k", value.lower()):
        return value.lower()
    raise argparse.ArgumentTypeError("must be 'copy' or a bitrate such as 96k, 128k, 192k")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False,
        usage="%(prog)s input_dir [-o OUTPUT] [-a {copy|BITRATE}] [-y] [--ffmpeg PATH] [--no-install] [-h]",
    )
    ap.add_argument("input_dir", help="(required) folder containing the MP3 files")
    opt = ap.add_argument_group("optional arguments")
    opt.add_argument("-o", "--output", metavar="OUTPUT",
                     help="output .m4b path (default: <input_dir>\\<title>.m4b)")
    opt.add_argument("-a", "--audio", metavar="{copy|BITRATE}", type=audio_setting, default="copy",
                     help="audio handling (default: copy). 'copy' keeps the original MP3 audio untouched "
                          "(lossless), but some players (e.g. Apple Books) may not support MP3 in M4B. "
                          "A bitrate such as 128k or 192k re-encodes to AAC for broader player support")
    opt.add_argument("-y", "--overwrite", action="store_true", help="overwrite the output file if it exists")
    opt.add_argument("--ffmpeg", metavar="PATH",
                     help="path to the ffmpeg binary or its folder (default: look on PATH and in common install locations)")
    opt.add_argument("--no-install", action="store_true",
                     help="don't try to install ffmpeg if it is missing (default: install automatically)")
    opt.add_argument("-h", "--help", action="help", help="show this help message and exit")
    args = ap.parse_args()

    folder = os.path.abspath(args.input_dir)
    ffmpeg, ffprobe = locate_ffmpeg(args.ffmpeg, not args.no_install)

    files = sorted(
        (os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".mp3")),
        key=lambda p: natural_key(os.path.basename(p)),
    )
    if not files:
        sys.exit(f"error: no mp3 files in {folder}")

    meta = load_metadata(folder)
    probes = [run_probe(ffprobe, f) for f in files]
    durations = [float(p["format"]["duration"]) for p in probes]
    tags = {k.lower(): v for k, v in probes[0]["format"].get("tags", {}).items()}

    title = meta.get("title") or tags.get("album") or os.path.basename(folder)
    creators = meta.get("creator", [])
    authors = [c["name"] for c in creators if c.get("role") == "author"] or ([tags["artist"]] if "artist" in tags else [])
    narrators = [c["name"] for c in creators if c.get("role") == "narrator"]
    description = strip_html((meta.get("description") or {}).get("full") or tags.get("comment", ""))
    cover = find_cover(folder)
    chapters = build_chapters(meta, files, durations)

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)
    output = os.path.abspath(args.output or os.path.join(folder, safe_title + ".m4b"))
    if os.path.exists(output) and not args.overwrite:
        sys.exit(f"error: {output} exists (use -y to overwrite)")

    with tempfile.TemporaryDirectory() as tmp:
        list_path = os.path.join(tmp, "files.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in files:
                f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")

        meta_path = os.path.join(tmp, "ffmetadata.txt")
        lines = [";FFMETADATA1", f"title={escape_meta(title)}", f"album={escape_meta(title)}", "genre=Audiobook"]
        if authors:
            lines += [f"artist={escape_meta(', '.join(authors))}", f"album_artist={escape_meta(', '.join(authors))}"]
        if narrators:
            lines.append(f"composer={escape_meta(', '.join(narrators))}")
        if description:
            lines.append(f"comment={escape_meta(description)}")
        for start, end, ctitle in chapters:
            lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(end * 1000)}",
                      f"title={escape_meta(ctitle)}"]
        with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

        cmd = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-i", meta_path]
        if cover:
            cmd += ["-i", cover]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover:
            cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        if args.audio == "copy":
            cmd += ["-c:a", "copy", "-f", "mp4"]
        else:
            cmd += ["-c:a", "aac", "-b:a", args.audio, "-f", "ipod"]
        cmd += ["-movflags", "+faststart", output]

        print(f"Converting {len(files)} files -> {output}")
        for start, _, ctitle in chapters:
            print(f"  {fmt_time(start)}  {ctitle}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            if os.path.exists(output):
                os.remove(output)
            sys.exit("error: ffmpeg failed")

    print(f"Done: {output} ({os.path.getsize(output) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
