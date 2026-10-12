"""Pick the 10 best highlight windows — heuristic scoring, no LLM.

Each candidate window (roughly 15-40s, adapted to video length) is
scored on signals that correlate with "short-worthy" moments:

* questions / hooks  — "what", "why", "how", "?" ...
* exclamations        — "!" (excitement peaks)
* numbers & superlatives — "3 reasons", "best", "first" ...
* emotional words     — "amazing", "secret", "never" ...
* speech density      — busy talking beats dead air
* opening bonus       — strong hooks live near the start

Selection is greedy and deterministic: highest score first, skipping
windows that overlap an already-picked one, with at most 3 picks per
quarter so the shorts spread across the whole video.
"""
import re

# Words that tend to open a compelling hook.
HOOK_WORDS = {
    "what", "why", "how", "when", "where", "which", "who",
    "secret", "secrets", "never", "stop", "warning", "mistake",
    "mistakes", "truth", "lies", "actually", "imagine", "look",
    "listen", "watch", "wait", "hold",
}
EMOTIONAL_WORDS = {
    "amazing", "incredible", "shocking", "unbelievable", "crazy",
    "insane", "beautiful", "terrible", "horrible", "love", "hate",
    "wow", "mystery", "hidden", "exposed", "revealed", "discovered",
    "destroyed", "epic", "genius", "stupid",
}
SUPERLATIVES = {
    "best", "worst", "biggest", "smallest", "fastest", "slowest",
    "first", "last", "most", "least", "greatest", "ultimate",
    "only", "ever",
}
_WORD_RE = re.compile(r"[a-z']+")


def _tokens(text):
    return _WORD_RE.findall(text.lower())


def _window_text(words, start, end):
    """All words (with timings) inside [start, end)."""
    return [w for w in words if w["s"] >= start and w["s"] < end]


def _score_window(win_words, start, end, duration):
    text = " ".join(w["w"] for w in win_words)
    toks = _tokens(text)
    n = len(toks)
    if n == 0:
        return 0.0, "silent"

    questions = text.count("?") + sum(1 for t in toks if t in HOOK_WORDS)
    exclaims = text.count("!")
    numbers = len(re.findall(r"\d+", text))
    supers = sum(1 for t in toks if t in SUPERLATIVES)
    emotion = sum(1 for t in toks if t in EMOTIONAL_WORDS)

    # Speech density: words per second, capped; silence is penalised.
    wps = n / max(end - start, 0.1)
    density = min(wps, 3.5) * 2.0

    score = (questions * 3.0 + exclaims * 2.0 + numbers * 1.5
             + supers * 1.0 + emotion * 1.0 + density)
    if wps < 0.8:
        score *= 0.3  # mostly dead air

    reasons = []
    if questions:
        reasons.append(f"{questions} hook/question")
    if exclaims:
        reasons.append(f"{exclaims} exclamation")
    if numbers:
        reasons.append(f"{numbers} number")
    if supers:
        reasons.append("superlative")
    if emotion:
        reasons.append("emotional")
    if start < 0.10 * duration:
        score += 4.0  # strong openings make great shorts
        reasons.append("strong opening")

    return round(score, 2), ", ".join(reasons) or "steady speech"


def _candidates(words, duration, win_len, step):
    cands = []
    t = 0.0
    while t + win_len <= duration + 0.01:
        end = min(t + win_len, duration)
        win_words = _window_text(words, t, end)
        score, reason = _score_window(win_words, t, end, duration)
        cands.append({"start": round(t, 2), "end": round(end, 2),
                      "score": score, "reason": reason})
        t += step
    return cands


def _select(cands, duration, count, min_gap, per_quarter):
    # Highest score first; ties break by earlier start (deterministic).
    ordered = sorted(cands, key=lambda c: (-c["score"], c["start"]))
    picked = []
    quarter_of = lambda s: min(3, int(s / duration * 4)) if duration else 0
    quarter_counts = [0, 0, 0, 0]
    for c in ordered:
        if len(picked) >= count:
            break
        if any(not (c["end"] + min_gap <= p["start"]
                    or c["start"] >= p["end"] + min_gap) for p in picked):
            continue
        if quarter_counts[quarter_of(c["start"])] >= per_quarter:
            continue
        picked.append(c)
        quarter_counts[quarter_of(c["start"])] += 1
    return picked


def find_highlights(segments, duration, count=10):
    """Return ``count`` highlight windows: [{start, end, score, reason}].

    Sorted chronologically. Never raises on odd input — worst case it
    returns evenly spread windows.
    """
    words = [w for seg in segments for w in seg.get("words", [])]
    if duration <= 0:
        return []

    win_len = max(15.0, min(40.0, duration / 12.0))
    step = max(3.0, win_len / 4.0)

    cands = _candidates(words, duration, win_len, step)
    if not cands:  # degenerate: fall back to plain slices
        n = min(count, max(1, int(duration // 15)))
        return [{"start": round(i * duration / n, 2),
                 "end": round((i + 1) * duration / n, 2),
                 "score": 0.0, "reason": "even split (no speech found)"}
                for i in range(n)]

    picked = _select(cands, duration, count, min_gap=2.0, per_quarter=3)
    if len(picked) < count:
        # Relaxed second pass: allow tighter packing to fill the quota.
        picked = _select(cands, duration, count, min_gap=0.5, per_quarter=4)
    if len(picked) < count:
        # Fill passes: shorter windows on a fine grid slot into the gaps
        # the full-size windows could not fill.
        for fill_len in (15.0, 10.0):
            if len(picked) >= count:
                break
            shorts = _candidates(words, duration, fill_len, 3.0)
            picked = _select_fill(shorts, picked, duration, count)

    picked.sort(key=lambda c: c["start"])
    return picked[:count]


def _select_fill(cands, picked, duration, count):
    """Add short-window candidates that avoid every picked window."""
    ordered = sorted(cands, key=lambda c: (-c["score"], c["start"]))
    out = list(picked)
    for c in ordered:
        if len(out) >= count:
            break
        if any(not (c["end"] + 1.0 <= p["start"]
                    or c["start"] >= p["end"] + 1.0) for p in out):
            continue
        out.append(c)
    return out
