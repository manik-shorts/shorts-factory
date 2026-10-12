"""Render one vertical (1080x1920) Short with burned-in karaoke subtitles.

The source clip is center-cropped to 9:16 (with a slight upward bias so
faces stay in frame), upscaled to 1080x1920, and the ASS karaoke file is
burned in. Output has exactly one H.264 video stream and one AAC audio
stream.
"""
import subprocess


def _escape_filter_path(path):
    # Inside single quotes in a filtergraph, escape ' and handle ':'.
    return path.replace("'", r"'\''").replace(":", r"\:")


def render_short(source_path, start, end, ass_path, out_path):
    """Cut [start, end) from source, render vertical MP4 to out_path."""
    duration = max(1.0, end - start)

    # 9:16 crop that works for landscape, square and portrait sources.
    # ow/oh are forced even (required by yuv420p); y gets a -40px upward
    # bias, clamped at 0 so it never goes negative.
    crop = (
        "crop="
        "w='floor(min(iw,ih*9/16)/2)*2':"
        "h='floor(min(ih,iw*16/9)/2)*2':"
        "x='(iw-ow)/2':"
        "y='max(0,(ih-oh)/2-40)'"
    )
    vf = (f"{crop},scale=1080:1920:flags=lanczos,setsar=1,"
          f"ass='{_escape_filter_path(ass_path)}'")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.2f}",
        "-i", source_path,
        "-t", f"{duration:.2f}",
        "-vf", vf,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg render failed for {out_path}\n{proc.stderr[-2000:]}")
    return out_path
