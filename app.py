#!/usr/bin/env python3
"""Shorts Factory — web app.

Paste a YouTube URL -> get 10 vertical Shorts with karaoke subtitles.
100% free stack: yt-dlp + faster-whisper + ffmpeg. No API keys.

Run:
    python app.py
Then open http://localhost:7860 (mobile-friendly).
(Set PORT=5000 in the environment to use the old port.)

Accounts: signup/login with email + password (hashed). The generate
feature requires login. Access is controlled by ONE gating function,
``user_can_generate()`` — see below.
"""
import json
import glob
import os
import secrets
import sqlite3
import threading
import time
import uuid

from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, send_file, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from werkzeug.security import check_password_hash, generate_password_hash

from pipeline.runner import run_pipeline
from pipeline.subtitles import STYLES, DEFAULT_STYLE
from pipeline.inspect import (fetch_metadata, extract_hashtags,
                              fetch_captions, segments_to_txt,
                              segments_to_srt, coalesce, fmt_clock)
from pipeline.transcribe import transcribe
import pipeline.download as _dl  # for YTDLP/_run in the audio fallback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "shorts_factory.db")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")

app = Flask(__name__)

# --- secret key (persisted so logins survive restarts) ---------------------
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
_key_file = os.path.join(BASE_DIR, "instance", "secret.key")
if os.path.exists(_key_file):
    with open(_key_file, "rb") as fh:
        app.secret_key = fh.read().strip()
else:
    app.secret_key = secrets.token_hex(32).encode()
    with open(_key_file, "wb") as fh:
        fh.write(app.secret_key)


# --- database ---------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.name = row["name"]
        self.email = row["email"]


login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?",
                           (user_id,)).fetchone()
    return User(row) if row else None


# --- access gate (single place to extend later) -----------------------------
def user_can_generate(user):
    """Return True if this user may use the generate feature.

    CURRENT POLICY: any logged-in user can generate.
    FUTURE: the owner plans "watch N ads -> 24h free access" logic.
    Put that logic HERE (e.g. check an ad_watches table + timestamp)
    so the route code below never needs to change. Do not build ads
    in v1.
    """
    return user is not None and user.is_authenticated


# --- background jobs ---------------------------------------------------------
jobs = {}  # job_id -> dict(stage, done, total, error, result, user_id)


def _job_worker(job_id, url, count, style):
    job = jobs[job_id]
    work_dir = os.path.join(JOBS_DIR, job_id)

    def progress(stage, done, total):
        job.update(stage=stage, done=done, total=total)

    try:
        result = run_pipeline(url, work_dir, count=count, style=style,
                              progress=progress)
        job["result"] = result
        job["stage"] = "done"
    except RuntimeError as exc:
        job["error"] = str(exc)
        job["stage"] = "error"
    except Exception as exc:  # never leave the UI hanging
        job["error"] = f"Unexpected error: {exc}"
        job["stage"] = "error"


def _job_or_404(job_id):
    job = jobs.get(job_id)
    if not job or job["user_id"] != current_user.id:
        return None
    return job


# --- HTML --------------------------------------------------------------------
CSS = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,Segoe UI,Roboto,Arial,sans-serif;
     margin:0;background:#0f0f14;color:#f2f2f5}
.wrap{max-width:560px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:26px;margin:8px 0 4px}
p.sub{color:#a7a7b3;margin:0 0 18px}
.card{background:#1a1a22;border:1px solid #2a2a35;border-radius:14px;
      padding:18px;margin:14px 0}
input[type=text],input[type=url],input[type=email],input[type=password]{
  width:100%;padding:14px;border-radius:10px;border:1px solid #33333f;
  background:#101016;color:#fff;font-size:16px;margin:6px 0}
select{width:100%;padding:14px;border-radius:10px;border:1px solid #33333f;
  background:#101016;color:#fff;font-size:16px;margin:6px 0}
label.lbl{display:block;font-size:13px;color:#a7a7b3;margin:10px 0 2px}
button,.btn{display:block;width:100%;padding:14px;border:0;border-radius:10px;
  background:#e62117;color:#fff;font-size:17px;font-weight:700;margin:10px 0;
  text-align:center;text-decoration:none;cursor:pointer}
button:disabled{background:#555}
a{color:#7ab8ff}
.top{display:flex;justify-content:space-between;align-items:center}
.top .u{color:#a7a7b3;font-size:14px}
.bar{height:10px;background:#2a2a35;border-radius:6px;overflow:hidden;margin:12px 0}
.bar>div{height:100%;background:#e62117;width:0%;transition:width .4s}
video{width:100%;max-height:60vh;border-radius:12px;background:#000}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid .card{padding:10px;margin:0}
.grid video{max-height:38vh}
.err{background:#3a1215;border:1px solid #7a1f1f;color:#ffb4b4;
     padding:12px;border-radius:10px}
.note{font-size:13px;color:#a7a7b3}
</style>
"""

NAV = """
<div class="top"><h1>🎬 Shorts Factory</h1>
  <span class="u">{{user}} · <a href="/logout">logout</a></span></div>
<nav style="display:flex;gap:8px;margin:10px 0 4px">
  <a class="btn" style="padding:10px;font-size:15px;margin:0" href="/">🎬 Shorts</a>
  <a class="btn" style="padding:10px;font-size:15px;margin:0;background:#33333f"
     href="/transcript">📝 Transcript</a>
  <a class="btn" style="padding:10px;font-size:15px;margin:0;background:#33333f"
     href="/inspect">🔍 Inspect</a>
</nav>
"""

HOME = CSS + """
<div class="wrap">
""" + NAV + """
  <p class="sub">Paste a YouTube link → get 10 vertical Shorts with subtitles.</p>
  <div class="card">
    <form action="/generate" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <label class="lbl" for="style">💬 Subtitle style</label>
      <select name="style" id="style">
        {{style_options|safe}}
      </select>
      <button type="submit">⚡ Generate 10 Shorts</button>
    </form>
    <p class="note">100% free: yt-dlp · faster-whisper · ffmpeg. No API keys.</p>
  </div>
</div>
"""

LANDING = CSS + """
<div class="wrap">
  <h1>🎬 Shorts Factory</h1>
  <p class="sub">Turn any YouTube video into 10 vertical Shorts with karaoke subtitles — free.</p>
  <div class="card">
    <a class="btn" href="/login">Log in</a>
    <a class="btn" href="/signup" style="background:#33333f">Create account</a>
  </div>
</div>
"""

SIGNUP = CSS + """
<div class="wrap"><h1>Create account</h1>
{% if err %}<div class="err">{{err}}</div>{% endif %}
<div class="card"><form method="post">
  <input type="text" name="name" required placeholder="Your name">
  <input type="email" name="email" required placeholder="Email">
  <input type="password" name="password" required minlength="6"
         placeholder="Password (min 6 chars)">
  <button type="submit">Sign up</button>
</form><p class="note">Already have an account? <a href="/login">Log in</a></p></div></div>
"""

LOGIN = CSS + """
<div class="wrap"><h1>Log in</h1>
{% if err %}<div class="err">{{err}}</div>{% endif %}
<div class="card"><form method="post">
  <input type="email" name="email" required placeholder="Email">
  <input type="password" name="password" required placeholder="Password">
  <button type="submit">Log in</button>
</form><p class="note">No account yet? <a href="/signup">Sign up</a></p></div></div>
"""

PROGRESS = CSS + """
<div class="wrap"><h1>Working… ⏳</h1>
<p class="sub" id="msg">Starting…</p>
<div class="bar"><div id="fill"></div></div>
<p class="note">This can take several minutes (download + AI transcription +
rendering 10 clips). Keep this page open.</p></div>
<script>
const jid="{{jid}}";
async function tick(){
  const r=await fetch("/status/"+jid); const s=await r.json();
  document.getElementById("msg").textContent=
    s.stage==="error" ? "Error: "+s.error :
    s.stage==="done"  ? "Done!" : "Stage: "+s.stage+" ("+s.done+"/"+s.total+")";
  if(s.total>0) document.getElementById("fill").style.width=
    Math.round(100*s.done/Math.max(s.total,1))+"%";
  if(s.stage==="done") location.href="/results/"+jid;
  else if(s.stage!=="error") setTimeout(tick,2000);
}
tick();
</script>
"""

RESULTS = CSS + """
<div class="wrap">
  <div class="top"><h1>✅ Your 10 Shorts</h1>
    <span class="u">{{user}} · <a href="/">new</a> · <a href="/logout">logout</a></span></div>
  <p class="sub">{{title}}<br>💬 Subtitles: {{style_label}}</p>
  <div class="grid">
  {% for c in clips %}
    <div class="card">
      <video src="/download/{{jid}}/{{c.file}}" controls preload="metadata" playsinline></video>
      <p class="note">#{{loop.index}} · {{c.start}}s–{{c.end}}s<br>{{c.reason}}</p>
      <a class="btn" style="padding:10px;font-size:15px"
         href="/download/{{jid}}/{{c.file}}" download>⬇ Download</a>
    </div>
  {% endfor %}
  </div>
</div>
"""

GATE_DENIED = CSS + """
<div class="wrap"><h1>⏳ Free access expired</h1>
<p class="sub">Your free generation window has ended. Watch a short ad to
unlock another 24 hours — coming soon.</p>
<div class="card"><a class="btn" href="/">Back home</a></div></div>
"""

TRANSCRIPT_FORM = CSS + """
<div class="wrap">
""" + NAV + """
  <p class="sub">Paste a YouTube link → get its full transcript.</p>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card">
    <form action="/transcript" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <button type="submit">📝 Get transcript</button>
    </form>
    <p class="note">Tries YouTube's own captions first (fast, no download).
    Falls back to AI transcription when a video has no captions.</p>
  </div>
</div>
"""

TRANSCRIPT_RESULT = CSS + """
<div class="wrap">
""" + NAV + """
  <p class="sub">{{title}}<br>
  <span class="note">Source: <b>{{src}}</b> · {{n}} lines</span></p>
  <div class="card" style="display:flex;gap:8px">
    <button id="copybtn" style="margin:0">📋 Copy</button>
    <a class="btn" style="margin:0" href="/transcript/file/{{token}}.txt" download>⬇ .txt</a>
    <a class="btn" style="margin:0;background:#33333f"
       href="/transcript/file/{{token}}.srt" download>⬇ .srt</a>
  </div>
  <div class="card" id="tbox">
  {% for s in lines %}
    <p style="margin:8px 0;line-height:1.5">
      <a href="#" class="ts" data-t="{{s.t}}"
         style="color:#7ab8ff;text-decoration:none;font-size:13px">{{s.clock}}</a>
      &nbsp;{{s.text}}</p>
  {% endfor %}
  </div>
</div>
<script>
document.getElementById("copybtn").onclick=async()=>{
  const t=[...document.querySelectorAll("#tbox p")]
    .map(p=>p.innerText).join("\\n");
  await navigator.clipboard.writeText(t);
  document.getElementById("copybtn").textContent="✅ Copied!";
};
document.querySelectorAll(".ts").forEach(a=>{a.onclick=e=>{
  e.preventDefault();
  navigator.clipboard.writeText(a.dataset.t);
  a.textContent="✓";
};});
</script>
"""

TPROGRESS = CSS + """
<div class="wrap"><h1>Transcribing… ⏳</h1>
<p class="sub" id="msg">This video has no YouTube captions — running AI
transcription (a few minutes). Keep this page open.</p>
<div class="bar"><div id="fill" style="width:30%"></div></div></div>
<script>
const jid="{{jid}}";
async function tick(){
  const r=await fetch("/tstatus/"+jid); const s=await r.json();
  document.getElementById("msg").textContent=
    s.stage==="error" ? "Error: "+s.error : s.msg;
  if(s.stage==="done") location.href="/transcript/view/"+s.token;
  else if(s.stage!=="error") setTimeout(tick,3000);
}
tick();
</script>
"""

INSPECT_FORM = CSS + """
<div class="wrap">
""" + NAV + """
  <p class="sub">Paste a YouTube link → see its title, tags, hashtags and
  description. Handy for competitor research.</p>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card">
    <form action="/inspect" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <button type="submit">🔍 Inspect video</button>
    </form>
    <p class="note">Reads public metadata only — nothing is downloaded.</p>
  </div>
</div>
"""

INSPECT_RESULT = CSS + """
<div class="wrap">
""" + NAV + """
  <div class="card">
    <h1 style="font-size:20px">{{meta.title}}</h1>
    <p class="sub">{{meta.channel}} · {{meta.duration_str}} ·
       👁 {{meta.view_count}} · 👍 {{meta.like_count}} ·
       📅 {{meta.upload_date}}</p>
    <details><summary style="cursor:pointer;color:#7ab8ff">Full description
      ({{meta.description|length}} chars)</summary>
      <p class="note" style="white-space:pre-wrap">{{meta.description}}</p>
    </details>
  </div>
  <div class="card">
    <div class="top"><h1 style="font-size:18px">🏷 Tags ({{meta.tags|length}})</h1>
      <button id="ctags" style="width:auto;padding:8px 14px;margin:0;font-size:14px">Copy all</button></div>
    <p id="tagbox" style="line-height:2.1">
    {% for t in meta.tags %}<span class="chip">{{t}}</span> {% else %}
      <span class="note">No tags on this video.</span>{% endfor %}</p>
  </div>
  <div class="card">
    <div class="top"><h1 style="font-size:18px"># Hashtags ({{hashtags|length}})</h1>
      <button id="chash" style="width:auto;padding:8px 14px;margin:0;font-size:14px">Copy all</button></div>
    <p id="hashbox" style="line-height:2.1">
    {% for h in hashtags %}<span class="chip">{{h}}</span> {% else %}
      <span class="note">No hashtags found.</span>{% endfor %}</p>
  </div>
  <p class="note"><a href="{{meta.webpage_url}}">Open on YouTube ↗</a></p>
</div>
<style>.chip{display:inline-block;background:#2a2a35;border:1px solid #3a3a48;
  border-radius:20px;padding:4px 12px;font-size:14px;margin:2px;cursor:pointer}
  .chip:active{background:#3a3a48}</style>
<script>
function chips(id){return [...document.querySelectorAll("#"+id+" .chip")]
  .map(c=>c.textContent);}
document.getElementById("ctags").onclick=async e=>{
  await navigator.clipboard.writeText(chips("tagbox").join(", "));
  e.target.textContent="✅ Copied!";};
document.getElementById("chash").onclick=async e=>{
  await navigator.clipboard.writeText(chips("hashbox").join(" "));
  e.target.textContent="✅ Copied!";};
document.querySelectorAll(".chip").forEach(c=>{c.onclick=async()=>{
  await navigator.clipboard.writeText(c.textContent);};});
</script>
"""


# --- transcript + inspect -----------------------------------------------------
# transcript results: token -> {user_id, title, lines, source, url}
transcripts = {}
# transcript AI-fallback jobs: job_id -> {user_id, stage, msg, error, token}
tjobs = {}


def _store_transcript(user_id, title, lines, source, url):
    token = uuid.uuid4().hex[:12]
    transcripts[token] = {"user_id": user_id, "title": title,
                          "lines": lines, "source": source, "url": url}
    return token


def _transcript_view_lines(lines):
    return [{"t": fmt_clock(s["start"]), "clock": fmt_clock(s["start"]),
             "text": s["text"]} for s in lines]


def _download_audio_only(url, work_dir):
    """Download best audio only (for the AI transcript fallback)."""
    os.makedirs(work_dir, exist_ok=True)
    out_tpl = os.path.join(work_dir, "audio.%(ext)s")
    last_err = ""
    for extra in ((), ("--extractor-args", "youtube:player_client=android")):
        rc, _, err = _dl._run(_dl.YTDLP + [
            "--no-playlist", "-f", "bestaudio/best",
            "--retries", "10", "--socket-timeout", "30",
            "-o", out_tpl, *extra, url,
        ])
        if rc == 0:
            break
        last_err = err
    else:
        tail = "\n".join(last_err.splitlines()[-4:])
        raise RuntimeError(f"Audio download failed\n{tail}")
    cands = [p for p in glob.glob(os.path.join(work_dir, "audio.*"))
             if not p.endswith(".part")]
    if not cands:
        raise RuntimeError("Audio download produced no file")
    return sorted(cands)[0]


def _tjob_worker(job_id, url):
    job = tjobs[job_id]
    work_dir = os.path.join(BASE_DIR, "transcript_jobs", job_id)
    try:
        job["msg"] = "Downloading audio…"
        audio = _download_audio_only(url, work_dir)
        job["msg"] = "AI transcribing (this takes a few minutes)…"
        model_name = os.environ.get("WHISPER_MODEL", "base")
        segs = transcribe(audio, work_dir, model_name=model_name)
        lines = coalesce(
            [{"start": s["start"], "end": s["end"], "text": s["text"]}
             for s in segs if s["text"].strip()])
        try:
            meta = fetch_metadata(url)
            title = meta["title"]
        except Exception:
            title = url
        token = _store_transcript(job["user_id"], title, lines,
                                  "AI transcribed", url)
        job["token"] = token
        job["stage"] = "done"
        job["msg"] = "Done!"
    except RuntimeError as exc:
        job["error"] = str(exc)
        job["stage"] = "error"
    except Exception as exc:
        job["error"] = f"Unexpected error: {exc}"
        job["stage"] = "error"


@app.route("/transcript", methods=["GET", "POST"])
@login_required
def transcript():
    if not user_can_generate(current_user):
        return render_template_string(GATE_DENIED), 403
    if request.method == "GET":
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name)
    url = request.form.get("url", "").strip()
    if not url or "http" not in url:
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name,
                                      err="Please paste a valid YouTube URL.")
    work_dir = os.path.join(BASE_DIR, "transcript_jobs",
                            "fast_" + uuid.uuid4().hex[:8])
    try:
        lines, source = fetch_captions(url, work_dir)
    except RuntimeError as exc:
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name,
                                      err=str(exc))
    if lines:
        try:
            meta = fetch_metadata(url)
            title = meta["title"]
        except Exception:
            title = url
        token = _store_transcript(current_user.id, title, lines, source, url)
        return redirect(url_for("transcript_view", token=token))
    # No captions — fall back to AI transcription in the background.
    job_id = uuid.uuid4().hex[:12]
    tjobs[job_id] = {"user_id": current_user.id, "stage": "working",
                     "msg": "Starting…", "error": None, "token": None}
    t = threading.Thread(target=_tjob_worker, args=(job_id, url), daemon=True)
    t.start()
    return redirect(url_for("tprogress", job_id=job_id))


def _tjob_or_404(job_id):
    job = tjobs.get(job_id)
    if not job or job["user_id"] != current_user.id:
        return None
    return job


@app.route("/tprogress/<job_id>")
@login_required
def tprogress(job_id):
    if not _tjob_or_404(job_id):
        return redirect(url_for("transcript"))
    return render_template_string(TPROGRESS, jid=job_id)


@app.route("/tstatus/<job_id>")
@login_required
def tstatus(job_id):
    job = _tjob_or_404(job_id)
    if not job:
        return jsonify({"stage": "error", "error": "not found"}), 404
    return jsonify({"stage": job["stage"], "msg": job["msg"],
                    "error": job["error"], "token": job["token"]})


@app.route("/transcript/view/<token>")
@login_required
def transcript_view(token):
    t = transcripts.get(token)
    if not t or t["user_id"] != current_user.id:
        return redirect(url_for("transcript"))
    return render_template_string(
        TRANSCRIPT_RESULT, user=current_user.name, token=token,
        title=t["title"], src=t["source"], n=len(t["lines"]),
        lines=_transcript_view_lines(t["lines"]))


@app.route("/transcript/file/<token>.txt")
@login_required
def transcript_txt(token):
    t = transcripts.get(token)
    if not t or t["user_id"] != current_user.id:
        return "Not found", 404
    body = segments_to_txt(t["lines"], title=t["title"])
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=transcript_{token}.txt"})


@app.route("/transcript/file/<token>.srt")
@login_required
def transcript_srt(token):
    t = transcripts.get(token)
    if not t or t["user_id"] != current_user.id:
        return "Not found", 404
    body = segments_to_srt(t["lines"])
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=transcript_{token}.srt"})


@app.route("/inspect", methods=["GET", "POST"])
@login_required
def inspect():
    if not user_can_generate(current_user):
        return render_template_string(GATE_DENIED), 403
    if request.method == "GET":
        return render_template_string(INSPECT_FORM, user=current_user.name)
    url = request.form.get("url", "").strip()
    if not url or "http" not in url:
        return render_template_string(INSPECT_FORM, user=current_user.name,
                                      err="Please paste a valid YouTube URL.")
    try:
        meta = fetch_metadata(url)
    except RuntimeError as exc:
        return render_template_string(INSPECT_FORM, user=current_user.name,
                                      err=str(exc))
    hashtags = extract_hashtags(meta["title"], meta["description"])
    return render_template_string(INSPECT_RESULT, user=current_user.name,
                                  meta=meta, hashtags=hashtags)


# --- routes -------------------------------------------------------------------
STYLE_OPTIONS = "".join(
    f'<option value="{k}"'
    f'{" selected" if k == DEFAULT_STYLE else ""}>'
    f'{v["label"]}</option>'
    for k, v in STYLES.items()
) + '<option value="none">🚫 No subtitles</option>'


@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template_string(LANDING)
    return render_template_string(HOME, user=current_user.name,
                                  style_options=STYLE_OPTIONS)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    err = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        if not name or not email or len(pw) < 6:
            err = "Please fill every field (password min 6 chars)."
        else:
            try:
                with db() as conn:
                    cur = conn.execute(
                        "INSERT INTO users (name, email, password_hash)"
                        " VALUES (?, ?, ?)",
                        (name, email, generate_password_hash(pw)))
                    user_id = cur.lastrowid
                with db() as conn:
                    row = conn.execute("SELECT * FROM users WHERE id = ?",
                                       (user_id,)).fetchone()
                login_user(User(row))
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                err = "That email is already registered. Try logging in."
    return render_template_string(SIGNUP, err=err)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    err = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?",
                               (email,)).fetchone()
        if row and check_password_hash(row["password_hash"], pw):
            login_user(User(row))
            return redirect(url_for("index"))
        err = "Wrong email or password."
    return render_template_string(LOGIN, err=err)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    if not user_can_generate(current_user):
        return render_template_string(GATE_DENIED), 403
    url = request.form.get("url", "").strip()
    style = request.form.get("style", DEFAULT_STYLE)
    if style not in STYLES and style != "none":
        style = DEFAULT_STYLE
    if not url or "http" not in url:
        return redirect(url_for("index"))
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"user_id": current_user.id, "stage": "starting",
                    "done": 0, "total": 1, "error": None, "result": None,
                    "style": style}
    t = threading.Thread(target=_job_worker, args=(job_id, url, 10, style),
                         daemon=True)
    t.start()
    return redirect(url_for("progress", job_id=job_id))


@app.route("/progress/<job_id>")
@login_required
def progress(job_id):
    if not _job_or_404(job_id):
        return redirect(url_for("index"))
    return render_template_string(PROGRESS, jid=job_id)


@app.route("/status/<job_id>")
@login_required
def status(job_id):
    job = _job_or_404(job_id)
    if not job:
        return jsonify({"stage": "error", "error": "not found"}), 404
    return jsonify({"stage": job["stage"], "done": job["done"],
                    "total": job["total"], "error": job["error"]})


@app.route("/results/<job_id>")
@login_required
def results(job_id):
    job = _job_or_404(job_id)
    if not job or job["stage"] != "done":
        return redirect(url_for("index"))
    m = job["result"]["manifest"]
    style_key = m.get("style", DEFAULT_STYLE)
    style_label = ("🚫 No subtitles" if style_key == "none"
                   else STYLES.get(style_key, {}).get("label", style_key))
    return render_template_string(RESULTS, jid=job_id, clips=m["clips"],
                                  title=m["title"], user=current_user.name,
                                  style_label=style_label)


@app.route("/health")
def health():
    """Readiness probe (used by Hugging Face Spaces)."""
    return jsonify({"status": "ok"})


@app.route("/download/<job_id>/<filename>")
@login_required
def download(job_id, filename):
    job = _job_or_404(job_id)
    if not job:
        return redirect(url_for("index"))
    # Only allow the generated short_XX.mp4 files — no path traversal.
    if not (filename.startswith("short_") and filename.endswith(".mp4")):
        return "Not found", 404
    path = os.path.join(JOBS_DIR, job_id, filename)
    if not os.path.exists(path):
        return "Not found", 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    init_db()
    os.makedirs(JOBS_DIR, exist_ok=True)
    # Hugging Face Spaces expects port 7860; override with PORT env var.
    # Local dev default is also 7860 — set PORT=5000 if you prefer the old one.
    port = int(os.environ.get("PORT", "7860"))
    print(f"Shorts Factory on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
