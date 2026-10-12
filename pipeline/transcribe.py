"""Transcribe the downloaded video with faster-whisper.

Uses the free, open "base" model with word-level timestamps so the
karaoke subtitles can highlight each word exactly when it is spoken.
The transcript is cached as ``transcript.json`` next to the video so
re-runs skip the (slow) transcription step.

Note: audio is decoded to a numpy array with ffmpeg instead of letting
faster-whisper open the file itself. This avoids a PyAV version quirk
(``av.open(..., metadata_errors=...)``) that breaks on some installs.
"""
import json
import os
import subprocess

import numpy as np
from faster_whisper import WhisperModel

_MODEL = None


def _resolve_model_path(model_name):
    """Find an already-downloaded model in the HuggingFace hub cache.

    Returns the local snapshot directory if present (so we never touch
    the network when the weights are already there), otherwise the plain
    model name and faster-whisper downloads it on first use.
    """
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache",
                             "huggingface", "hub")
    snap_dir = os.path.join(cache_dir,
                            f"models--Systran--faster-whisper-{model_name}",
                            "snapshots")
    if os.path.isdir(snap_dir):
        for snap in sorted(os.listdir(snap_dir)):
            path = os.path.join(snap_dir, snap)
            if os.path.isfile(os.path.join(path, "model.bin")):
                return path
    return model_name


def _get_model(model_name="base"):
    global _MODEL
    if _MODEL is None:
        # First run downloads ~150MB of model weights (one time, free).
        _MODEL = WhisperModel(_resolve_model_path(model_name),
                              device="cpu", compute_type="int8")
    return _MODEL


def _load_audio_16k(path):
    """Decode any media file to 16kHz mono float32 via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-ar", "16000", "-ac", "1", "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    return pcm / 32768.0


def transcribe(video_path, work_dir, model_name="base"):
    """Return [{start, end, text, words:[{w, s, e}]}].

    ``words`` carry per-word start/end times in seconds (absolute,
    relative to the start of the video).
    """
    cache = os.path.join(work_dir, "transcript.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)

    model = _get_model(model_name)
    audio = _load_audio_16k(video_path)
    segments, _info = model.transcribe(audio, word_timestamps=True)

    out = []
    for seg in segments:
        words = [
            {"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
            for w in (seg.words or []) if w.word.strip()
        ]
        if not words:
            continue
        out.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
            "words": words,
        })

    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out
