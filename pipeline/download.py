"""Download a YouTube video with yt-dlp.

Grabs the best MP4 at 1080p or below and saves it as ``source.mp4``
inside the job's working directory.

yt-dlp is invoked as ``sys.executable -m yt_dlp`` (a Python module)
rather than as a shell binary, so it works whether or not the virtual
environment is activated.
"""
import os
import re
import subprocess
import sys

YTDLP = [sys.executable, "-m", "yt_dlp"]


def _run(cmd):
    """Run a command, returning (returncode, stdout, stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def probe_duration(path):
    """Return media duration in seconds via ffprobe."""
    rc, out, _ = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    if rc != 0:
        raise RuntimeError(f"ffprobe could not read {path}")
    return float(out)


def _metadata(url):
    """Fetch title / duration / id without downloading."""
    for extra in ((), ("--extractor-args", "youtube:player_client=android")):
        rc, out, err = _run(YTDLP + [
            "--no-playlist", "--skip-download",
            "--print", "%(title)s\t%(duration)s\t%(id)s",
            *extra, url,
        ])
        if rc == 0 and "\t" in out:
            title, duration, vid = out.split("\t")
            return {"title": title, "duration": float(duration or 0),
                    "id": vid}
    # Surface the useful part of yt-dlp's error (last few lines).
    tail = "\n".join(err.splitlines()[-4:])
    raise RuntimeError(f"Could not read video info for {url}\n{tail}")


def _download_once(url, out_tpl, extra_args=()):
    cmd = YTDLP + [
        "--no-playlist",
        # Best video <=1080p + best audio, merged to mp4. Falls back
        # gracefully when a 1080p mp4 mux does not exist.
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
        "--merge-output-format", "mp4",
        "-o", out_tpl,
        # Be generous with retries: throttled networks often drop the
        # connection once or twice before a download completes.
        "--retries", "10",
        "--socket-timeout", "30",
        *extra_args,
        url,
    ]
    return _run(cmd)


def download_video(url, work_dir):
    """Download best <=1080p MP4 for ``url`` into ``work_dir``.

    Returns dict with keys: path, title, duration, video_id.
    Raises RuntimeError with a readable message on failure.

    Note: some networks/IPs get YouTube's "Sign in to confirm you're not
    a bot" block. If the normal client fails, we retry with the Android
    player client, which usually still works (it may top out at a lower
    resolution on such networks).
    """
    os.makedirs(work_dir, exist_ok=True)
    meta = _metadata(url)

    out_tpl = os.path.join(work_dir, "source.%(ext)s")
    rc, _, err = _download_once(url, out_tpl)
    if rc != 0:
        # Fallback for bot-check / throttled networks.
        rc, _, err2 = _download_once(
            url, out_tpl,
            extra_args=("--extractor-args", "youtube:player_client=android"))
        err = err2 if rc != 0 else err
    if rc != 0:
        tail = "\n".join(err.splitlines()[-6:])
        raise RuntimeError(f"Download failed for {url}\n{tail}")

    path = os.path.join(work_dir, "source.mp4")
    if not os.path.exists(path):
        # yt-dlp sometimes keeps another extension when merging is
        # skipped; accept whatever it produced.
        cands = [f for f in os.listdir(work_dir)
                 if f.startswith("source.") and not f.endswith(".part")]
        if not cands:
            raise RuntimeError("yt-dlp finished but no video file appeared.")
        path = os.path.join(work_dir, cands[0])

    duration = probe_duration(path) or meta["duration"]
    return {
        "path": path,
        "title": re.sub(r"\s+", " ", meta["title"]).strip(),
        "duration": duration,
        "video_id": meta["id"],
    }
