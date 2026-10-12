"""Orchestrate the full pipeline: download -> transcribe -> highlights
-> (subtitles + render) x N.

Used by both the CLI (run.py) and the web app (app.py).
"""
import os

from .download import download_video
from .transcribe import transcribe
from .highlights import find_highlights
from .subtitles import write_ass, STYLES
from .render import render_short


def run_pipeline(url, work_dir, count=10, style="karaoke", progress=None):
    """Run the whole pipeline.

    ``progress`` is an optional callable(stage: str, done: int, total: int)
    used by the web UI to report live progress. Stages: "download",
    "transcribe", "highlights", "render".

    ``style``: subtitle style preset — one of "karaoke" (default),
    "clean", "pop", "minimal" (see pipeline/subtitles.py STYLES).

    Returns dict: {title, duration, video_id, clips: [paths...]}.
    Raises RuntimeError with a readable message on failure.
    """
    if style not in STYLES:
        raise RuntimeError(f"Unknown subtitle style: {style!r} "
                           f"(choices: {', '.join(STYLES)})")
    def emit(stage, done=0, total=1):
        if progress:
            progress(stage, done, total)

    os.makedirs(work_dir, exist_ok=True)

    emit("download")
    meta = download_video(url, work_dir)
    emit("download", 1, 1)

    emit("transcribe")
    # Whisper model is configurable via the WHISPER_MODEL env var
    # ("base" default for quality; "tiny" for low-RAM hosts like
    # Render's free tier). Render sets this in render.yaml.
    model_name = os.environ.get("WHISPER_MODEL", "base")
    segments = transcribe(meta["path"], work_dir, model_name=model_name)
    emit("transcribe", 1, 1)

    emit("highlights")
    words = [w for seg in segments for w in seg.get("words", [])]
    clips = find_highlights(segments, meta["duration"], count=count)
    emit("highlights", 1, 1)

    out_paths = []
    for i, clip in enumerate(clips, 1):
        emit("render", i - 1, len(clips))
        ass_path = os.path.join(work_dir, f"short_{i:02d}.ass")
        out_path = os.path.join(work_dir, f"short_{i:02d}.mp4")
        write_ass(words, clip["start"], clip["end"], ass_path, style=style)
        render_short(meta["path"], clip["start"], clip["end"],
                     ass_path, out_path)
        out_paths.append(out_path)
        emit("render", i, len(clips))

    # Save a small manifest for the results page.
    manifest = {
        "title": meta["title"],
        "duration": meta["duration"],
        "video_id": meta["video_id"],
        "style": style,
        "clips": [
            {"file": os.path.basename(p), "start": c["start"],
             "end": c["end"], "score": c["score"], "reason": c["reason"]}
            for p, c in zip(out_paths, clips)
        ],
    }
    import json
    with open(os.path.join(work_dir, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    return {"title": meta["title"], "duration": meta["duration"],
            "video_id": meta["video_id"], "clips": out_paths,
            "manifest": manifest}
