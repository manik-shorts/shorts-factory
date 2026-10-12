"""Build styled (.ass) subtitles for one clip window.

Word timings from faster-whisper drive the subtitle timing. Four style
presets are available (see STYLES); the "karaoke" preset adds classic
word-by-word ``{\\k}`` highlight sweeps, while the others show each
full line during its spoken time range.
"""
import os

# All styles are tuned for 1080x1920 vertical video.
# ASS colours are &HAABBGGRR (alpha, blue, green, red).
STYLES = {
    "karaoke": {
        "label": "Karaoke — word-by-word highlight",
        "fontname": "Arial",
        "fontsize": 84,
        "primary": "&H0000FFFF",      # sung word: yellow
        "secondary": "&H00FFFFFF",    # unsung: white
        "outline_color": "&H80000000",
        "back_color": "&H80000000",
        "bold": -1, "outline": 4, "shadow": 1,
        "alignment": 2, "margin_v": 220,   # bottom-center
        "karaoke": True,
    },
    "clean": {
        "label": "Clean — plain white, no highlight",
        "fontname": "Arial",
        "fontsize": 76,
        "primary": "&H00FFFFFF",      # white
        "secondary": "&H00FFFFFF",     # white (no sweep)
        "outline_color": "&H80000000",
        "back_color": "&H80000000",
        "bold": -1, "outline": 3, "shadow": 1,
        "alignment": 2, "margin_v": 220,   # bottom-center
        "karaoke": False,
    },
    "pop": {
        "label": "Pop — big bold yellow",
        "fontname": "Arial",
        "fontsize": 96,
        "primary": "&H0000FFFF",      # yellow
        "secondary": "&H0000FFFF",    # yellow (no sweep)
        "outline_color": "&H00000000",     # solid black
        "back_color": "&H80000000",
        "bold": -1, "outline": 6, "shadow": 2,
        "alignment": 2, "margin_v": 220,   # bottom-center
        "karaoke": False,
    },
    "minimal": {
        "label": "Minimal — small, lower third",
        "fontname": "Arial",
        "fontsize": 60,
        "primary": "&H00FFFFFF",      # white
        "secondary": "&H00FFFFFF",    # white (no sweep)
        "outline_color": "&H80000000",
        "back_color": "&H80000000",
        "bold": 0, "outline": 2, "shadow": 0,
        "alignment": 2, "margin_v": 120,   # lower third
        "karaoke": False,
    },
}

DEFAULT_STYLE = "karaoke"


def _ass_header(style_name):
    """Build the ASS file header for one style preset."""
    cfg = STYLES[style_name]
    ass_style = style_name.capitalize()
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {ass_style},{cfg["fontname"]},{cfg["fontsize"]},{cfg["primary"]},{cfg["secondary"]},{cfg["outline_color"]},{cfg["back_color"]},{cfg["bold"]},0,0,0,100,100,1,0,1,{cfg["outline"]},{cfg["shadow"]},{cfg["alignment"]},60,60,{cfg["margin_v"]},1

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


def write_ass(words, clip_start, clip_end, ass_path, style=DEFAULT_STYLE):
    """Write a styled ASS subtitle file.

    ``words``: [{w, s, e}] with ABSOLUTE video times. Only words inside
    [clip_start, clip_end) are used; their times are shifted so the clip
    starts at 0.

    ``style``: one of "karaoke" (default), "clean", "pop", "minimal".
    "karaoke" renders word-by-word {\\k} highlight sweeps; the others
    show each full line during its spoken time range.
    """
    if style not in STYLES:
        raise ValueError(f"Unknown subtitle style: {style!r} "
                         f"(choices: {', '.join(STYLES)})")
    cfg = STYLES[style]
    ass_style = style.capitalize()

    # Clip to the window and shift to clip-relative time.
    rel = []
    for w in words:
        s = max(w["s"], clip_start) - clip_start
        e = min(w["e"], clip_end) - clip_start
        if e > s and s < (clip_end - clip_start):
            rel.append({"w": w["w"], "s": s, "e": e})

    events = []
    for line in _to_lines(rel):
        if cfg["karaoke"]:
            parts = []
            for w in line:
                # {\kN}: highlight sweeps across this word over N
                # centiseconds — the classic word-by-word Shorts style.
                dur_cs = max(1, int(round((w["e"] - w["s"]) * 100)))
                parts.append(f"{{\\k{dur_cs}}}{w['w']}")
            text = " ".join(parts)
        else:
            # Full line shown for the whole spoken range (still driven
            # by the word timings).
            text = " ".join(w["w"] for w in line)
        start = _ts(line[0]["s"])
        end = _ts(line[-1]["e"] + 0.35)  # linger a beat after the line
        events.append(
            f"Dialogue: 0,{start},{end},{ass_style},,0,0,0,,{text}")

    os.makedirs(os.path.dirname(os.path.abspath(ass_path)), exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as fh:
        fh.write(_ass_header(style))
        fh.write("\n".join(events))
        fh.write("\n")
    return ass_path
