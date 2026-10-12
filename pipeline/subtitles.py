"""Build karaoke (.ass) subtitles for one clip window.

Word timings from faster-whisper become ``{\\k}`` karaoke tags: each
word is white until it is spoken, then sweeps to yellow exactly on the
beat — the classic word-by-word Shorts style.
"""
import os

# Tuned for 1080x1920 vertical video.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,84,&H0000FFFF,&H00FFFFFF,&H80000000,&H80000000,-1,0,0,0,100,100,1,0,1,4,1,2,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

MAX_WORDS_PER_LINE = 6
MAX_CHARS_PER_LINE = 30


def _ts(seconds):
    """ASS timestamp H:MM:SS.cc (clamped at 0)."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(word):
    return word.replace("{", "").replace("}", "").strip()


def _to_lines(words):
    """Group words into subtitle lines (relative times)."""
    lines, cur, cur_len = [], [], 0
    for w in words:
        text = _clean(w["w"])
        if not text:
            continue
        if cur and (len(cur) >= MAX_WORDS_PER_LINE
                    or cur_len + len(text) + 1 > MAX_CHARS_PER_LINE):
            lines.append(cur)
            cur, cur_len = [], 0
        cur.append({**w, "w": text})
        cur_len += len(text) + 1
    if cur:
        lines.append(cur)
    return lines


def write_ass(words, clip_start, clip_end, ass_path):
    """Write a karaoke ASS file.

    ``words``: [{w, s, e}] with ABSOLUTE video times. Only words inside
    [clip_start, clip_end) are used; their times are shifted so the clip
    starts at 0.
    """
    # Clip to the window and shift to clip-relative time.
    rel = []
    for w in words:
        s = max(w["s"], clip_start) - clip_start
        e = min(w["e"], clip_end) - clip_start
        if e > s and s < (clip_end - clip_start):
            rel.append({"w": w["w"], "s": s, "e": e})

    events = []
    for line in _to_lines(rel):
        parts = []
        for w in line:
            # {\kN}: highlight sweeps across this word over N centiseconds.
            dur_cs = max(1, int(round((w["e"] - w["s"]) * 100)))
            parts.append(f"{{\\k{dur_cs}}}{w['w']}")
        text = " ".join(parts)
        start = _ts(line[0]["s"])
        end = _ts(line[-1]["e"] + 0.35)  # linger a beat after the line
        events.append(
            f"Dialogue: 0,{start},{end},Karaoke,,0,0,0,,{text}")

    os.makedirs(os.path.dirname(os.path.abspath(ass_path)), exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as fh:
        fh.write(ASS_HEADER)
        fh.write("\n".join(events))
        fh.write("\n")
    return ass_path
