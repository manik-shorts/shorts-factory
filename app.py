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
import os
import secrets
import sqlite3
import threading
import time
import uuid

from flask import (Flask, jsonify, redirect, render_template_string,
                   request, send_file, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from werkzeug.security import check_password_hash, generate_password_hash

from pipeline.runner import run_pipeline

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


def _job_worker(job_id, url, count):
    job = jobs[job_id]
    work_dir = os.path.join(JOBS_DIR, job_id)

    def progress(stage, done, total):
        job.update(stage=stage, done=done, total=total)

    try:
        result = run_pipeline(url, work_dir, count=count, progress=progress)
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

HOME = CSS + """
<div class="wrap">
  <div class="top"><h1>🎬 Shorts Factory</h1>
    <span class="u">{{user}} · <a href="/logout">logout</a></span></div>
  <p class="sub">Paste a YouTube link → get 10 vertical Shorts with karaoke subtitles.</p>
  <div class="card">
    <form action="/generate" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
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
  <p class="sub">{{title}}</p>
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


# --- routes -------------------------------------------------------------------
@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template_string(LANDING)
    return render_template_string(HOME, user=current_user.name)


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
    if not url or "http" not in url:
        return redirect(url_for("index"))
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"user_id": current_user.id, "stage": "starting",
                    "done": 0, "total": 1, "error": None, "result": None}
    t = threading.Thread(target=_job_worker, args=(job_id, url, 10),
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
    return render_template_string(RESULTS, jid=job_id, clips=m["clips"],
                                  title=m["title"], user=current_user.name)


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
