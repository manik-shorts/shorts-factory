#!/usr/bin/env python3
"""Shorts Factory — command-line interface.

Usage:
    python run.py <youtube_url> [--out dir] [--count 10]

Downloads the video, transcribes it, finds the best highlight moments
and renders vertical Shorts with karaoke subtitles into the output dir.
100% free stack: yt-dlp + faster-whisper + ffmpeg. No API keys.
"""
import argparse
import os
import shutil
import sys
import time

from pipeline.runner import run_pipeline


def main():
    ap = argparse.ArgumentParser(description="Shorts Factory CLI")
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument("--out", default="output",
                    help="output directory (default: ./output)")
    ap.add_argument("--count", type=int, default=10,
                    help="number of shorts to make (default: 10)")
    ap.add_argument("--work", default=None,
                    help="working directory (default: <out>/.work)")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    work_dir = os.path.abspath(args.work or os.path.join(out_dir, ".work"))
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()

    def progress(stage, done, total):
        if stage == "render" and total > 1:
            print(f"  [render] clip {done}/{total} ...", flush=True)
        else:
            print(f"  [{stage}] ...", flush=True)

    print(f"URL: {args.url}")
    try:
        result = run_pipeline(args.url, work_dir,
                              count=args.count, progress=progress)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nVideo: {result['title']} "
          f"({result['duration']:.0f}s, id={result['video_id']})")
    for src in result["clips"]:
        dst = os.path.join(out_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        print(f"  wrote {dst}")

    mins = (time.time() - t0) / 60
    print(f"\nDone: {len(result['clips'])} shorts in {mins:.1f} min -> {out_dir}")


if __name__ == "__main__":
    main()
