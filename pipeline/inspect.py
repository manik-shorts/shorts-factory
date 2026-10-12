"""Video inspector + transcript fetcher for Shorts Factory.

Two free, keyless features built on yt-dlp metadata:

- fetch_metadata(url): one ``--dump-json --skip-download`` call returning
  title, channel, duration, views, upload date, likes, TAGS and description.
- extract_hashtags(title, description): #[A-Za-z0-9_]+ via regex.
- fetch_captions(url, work_dir): FAST transcript path — downloads YouTube's
  own captions (manual or auto-generated) as VTT without downloading any
  video, then parses them into clean timestamped lines. Returns None when
  the video has no usable captions (caller should fall back to AI
  transcription via pipeline.transcribe).

All yt-dlp calls go through ``sys.executable -m yt_dlp`` and retry with
the Android player client on bot-check / throttled networks (same pattern
as pipeline/download.py).
"""
import glob
import json
import os
import re
import subprocess
import sys

YTDLP = [sys.executable, "-m", "yt_dlp"]

EXTRA_CLIENTS = (
    (),
    ("--extractor-args", "youtube:player_client=android"),
)


def _run(cmd):
    """Run a command, returning (returncode, stdout, stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _ytdlp_with_fallback(args):
    """Run yt-dlp args, retrying with the Android client on failure.

    Returns (rc, stdout, stderr) of the first successful attempt.
    Raises RuntimeError with a readable tail when all attempts fail.
    """
    last_err = ""
    for extra in EXTRA_CLIENTS:
        rc, out, err = _run(YTDLP + list(extra) + args)
        if rc == 0:
            return rc, out, err
        last_err = err
    tail = "\n".join(last_err.splitlines()[-4:])
    raise RuntimeError(f"yt-dlp failed\n{tail}")


# --- metadata ---------------------------------------------------------------

def _fmt_upload_date(raw):
    """'20210315' -> '2021-03-15' (pass through anything else)."""
    if raw and re.fullmatch(r"\d{8}", str(raw)):
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or "—"


def _fmt_num(n):
    if n is None:
        return "—"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def fetch_metadata(url):
    """Fetch video metadata with ONE ``--dump-json --skip-download`` call.

    Returns dict: id, title, channel, duration (seconds), duration_str,
    view_count, like_count, upload_date, tags (list), description.
    No video is downloaded. Raises RuntimeError on failure.
    """
    _, out, _ = _ytdlp_with_fallback(
        ["--no-playlist", "--skip-download", "--dump-json", url])
    info = json.loads(out)
    duration = info.get("duration") or 0
    return {
        "id": info.get("id", ""),
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel") or info.get("uploader") or "—",
        "duration": duration,
        "duration_str": fmt_duration(duration),
        "view_count": _fmt_num(info.get("view_count")),
        "like_count": _fmt_num(info.get("like_count")),
        "upload_date": _fmt_upload_date(info.get("upload_date")),
        "tags": [t for t in (info.get("tags") or []) if t],
        "description": info.get("description") or "",
        "webpage_url": info.get("webpage_url") or url,
    }


def fmt_duration(seconds):
    """3723 -> '1:02:03'; 95 -> '1:35'."""
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]+")


def extract_hashtags(title, description):
    """Find #hashtags in title + description, deduped, order-preserved."""
    seen = set()
    tags = []
    for text in (title or "", description or ""):
        for m in HASHTAG_RE.finditer(text):
            tag = m.group(0)
            # YouTube hashtags are case-insensitive for dedupe purposes,
            # but keep the first-seen spelling.
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags


# --- captions (fast transcript path) ----------------------------------------

def _vtt_ts_to_seconds(ts):
    """'00:01:23.456' -> 83.456"""
    h, m, rest = ts.split(":")
    s = rest.replace(",", ".")
    return int(h) * 3600 + int(m) * 60 + float(s)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_cue_text(raw):
    text = _TAG_RE.sub("", raw or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS_RE.sub(" ", text).strip()


def parse_vtt(path):
    """Parse a VTT file into [{start, end, text}] with cleaned text.

    Drops the WEBVTT header, cue settings, NOTE/STYLE blocks and HTML-ish
    tags. Consecutive cues with identical text are merged (extends end).
    """
    cues = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            try:
                start_s, _, end_s = line.partition("-->")
                start = _vtt_ts_to_seconds(start_s.strip().split(" ")[0])
                end = _vtt_ts_to_seconds(end_s.strip().split(" ")[0])
            except (ValueError, IndexError):
                i += 1
                continue
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i])
                i += 1
            text = _clean_cue_text(" ".join(text_lines))
            if text and end > start:
                if cues and cues[-1]["text"] == text:
                    cues[-1]["end"] = max(cues[-1]["end"], end)
                else:
                    cues.append({"start": round(start, 2),
                                 "end": round(end, 2), "text": text})
        else:
            i += 1
    return cues


def coalesce(cues, max_gap=1.0, max_chars=140):
    """Merge short cues into readable lines.

    Joins consecutive cues until a silence gap > max_gap or the line
    exceeds max_chars, producing [{start, end, text}] timeline lines.
    """
    lines = []
    for cue in cues:
        if (lines and cue["start"] - lines[-1]["end"] <= max_gap
                and len(lines[-1]["text"]) + 1 + len(cue["text"]) <= max_chars
                and not re.search(r"[.!?…]$", lines[-1]["text"])):
            lines[-1]["text"] += " " + cue["text"]
            lines[-1]["end"] = cue["end"]
        else:
            lines.append(dict(cue))
    return lines


def fetch_captions(url, work_dir):
    """FAST transcript path: YouTube's own captions, no video download.

    Downloads manual subs first, auto-generated subs as fallback
    (``--write-subs --write-auto-subs``), as VTT, then parses + coalesces
    them into clean timestamped lines.

    Returns (lines, source_label) where lines = [{start, end, text}] and
    source_label is "YouTube captions" — or (None, None) when the video
    has no usable captions (caller should use the AI fallback).
    Raises RuntimeError on network failure.
    """
    os.makedirs(work_dir, exist_ok=True)
    out_tpl = os.path.join(work_dir, "caps.%(ext)s")

    last_err = ""
    got_files = []
    for extra in EXTRA_CLIENTS:
        rc, _, err = _run(YTDLP + [
            "--no-playlist", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "en.*",
            "--sub-format", "vtt/best",
            "--retries", "5",
            "-o", out_tpl,
            *extra, url,
        ])
        got_files = sorted(glob.glob(os.path.join(work_dir, "caps*.vtt")))
        if rc == 0 and got_files:
            break
        last_err = err
    if not got_files:
        tail = "\n".join((last_err or "").splitlines()[-4:])
        # No captions is a normal outcome, not a crash — but a total
        # network failure should still be visible to the caller.
        if "ERROR" in (last_err or "") and "subtitles" not in (last_err or "").lower():
            raise RuntimeError(f"Caption fetch failed\n{tail}")
        return None, None

    # Prefer a plain ".en.vtt" (manual subs) over regional variants.
    def _rank(p):
        base = os.path.basename(p)
        return (0 if re.search(r"\.en\.vtt$", base) else 1, len(base))
    vtt_path = sorted(got_files, key=_rank)[0]

    cues = parse_vtt(vtt_path)
    if not cues:
        return None, None
    return coalesce(cues), "YouTube captions"


# --- export formats ----------------------------------------------------------

def fmt_clock(seconds):
    """83.4 -> '01:23' (or '1:02:03' past an hour)."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def segments_to_txt(lines, title=""):
    """Plain-text transcript: '[MM:SS] spoken text' per line."""
    out = []
    if title:
        out.append(title)
        out.append("")
    for seg in lines:
        out.append(f"[{fmt_clock(seg['start'])}] {seg['text']}")
    return "\n".join(out) + "\n"


def _srt_ts(seconds):
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(lines):
    """SubRip subtitle file content."""
    blocks = []
    for i, seg in enumerate(lines, 1):
        blocks.append(
            f"{i}\n{_srt_ts(seg['start'])} --> {_srt_ts(seg['end'])}\n"
            f"{seg['text']}\n")
    return "\n".join(blocks)
