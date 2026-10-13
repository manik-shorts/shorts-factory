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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#08080f; --card:rgba(255,255,255,.045); --line:rgba(255,255,255,.09);
  --txt:#f4f4fa; --mut:#9c9cb4; --acc1:#ff3d5a; --acc2:#a855f7;
  --grad:linear-gradient(135deg,#ff3d5a,#a855f7);
}
*{box-sizing:border-box}
body{font-family:Inter,-apple-system,system-ui,'Segoe UI',Roboto,Arial,sans-serif;
  margin:0;color:var(--txt);min-height:100vh;
  background:
    radial-gradient(900px 480px at 15% -5%,rgba(168,85,247,.16),transparent 60%),
    radial-gradient(800px 420px at 90% 10%,rgba(255,61,90,.12),transparent 60%),
    var(--bg);}
.wrap{max-width:640px;margin:0 auto;padding:22px 18px 56px}
h1{font-size:28px;font-weight:800;margin:10px 0 6px;letter-spacing:-.5px}
.grad{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
p.sub{color:var(--mut);margin:0 0 20px;font-size:15px;line-height:1.55}
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;
  padding:22px;margin:16px 0;backdrop-filter:blur(14px);
  box-shadow:0 12px 40px rgba(0,0,0,.35)}
input[type=text],input[type=url],input[type=email],input[type=password]{
  width:100%;padding:15px 16px;border-radius:14px;border:1px solid var(--line);
  background:rgba(0,0,0,.35);color:#fff;font-size:16px;margin:6px 0;
  font-family:inherit;transition:border .2s,box-shadow .2s}
input:focus{outline:none;border-color:var(--acc2);box-shadow:0 0 0 3px rgba(168,85,247,.25)}
label.lbl{display:block;font-size:13px;font-weight:600;color:var(--mut);
  margin:14px 0 8px;letter-spacing:.3px}
button,.btn{display:block;width:100%;padding:15px;border:0;border-radius:14px;
  background:var(--grad);color:#fff;font-size:16px;font-weight:700;margin:12px 0;
  text-align:center;text-decoration:none;cursor:pointer;font-family:inherit;
  box-shadow:0 8px 28px rgba(255,61,90,.35);transition:transform .15s,box-shadow .15s}
button:hover,.btn:hover{transform:translateY(-1px);box-shadow:0 12px 34px rgba(255,61,90,.45)}
button:disabled{background:#3a3a48;box-shadow:none;cursor:default}
.btn.ghost{background:rgba(255,255,255,.07);box-shadow:none;border:1px solid var(--line)}
a{color:#c4b5fd}
.nav{position:sticky;top:0;z-index:20;backdrop-filter:blur(16px);
  background:rgba(8,8,15,.72);border-bottom:1px solid var(--line);margin:-22px -18px 18px;
  padding:14px 18px}
.nav .row{display:flex;justify-content:space-between;align-items:center;max-width:640px;margin:0 auto}
.logo{font-weight:800;font-size:18px;letter-spacing:-.3px}
.logo span{background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}
.nav .u{color:var(--mut);font-size:13px}
.tabs{display:flex;gap:8px;margin-top:12px}
.tab{flex:1;text-align:center;padding:10px;border-radius:12px;font-size:14px;font-weight:600;
  color:var(--mut);text-decoration:none;background:rgba(255,255,255,.04);
  border:1px solid transparent;transition:all .2s}
.tab.on{color:#fff;background:rgba(168,85,247,.16);border-color:rgba(168,85,247,.4)}
.bar{height:12px;background:rgba(255,255,255,.07);border-radius:8px;overflow:hidden;margin:14px 0}
.bar>div{height:100%;background:var(--grad);width:0%;transition:width .4s;border-radius:8px}
video{width:100%;max-height:60vh;border-radius:14px;background:#000;display:block}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:480px){.grid{grid-template-columns:1fr}}
.scard{background:var(--card);border:1px solid var(--line);border-radius:18px;
  padding:12px;backdrop-filter:blur(14px);transition:transform .18s,box-shadow .18s}
.scard:hover{transform:translateY(-3px);box-shadow:0 16px 44px rgba(0,0,0,.45)}
.scard .meta{font-size:12.5px;color:var(--mut);margin:8px 2px;line-height:1.5}
.scard .btn{padding:11px;font-size:14px;margin:8px 0 2px}
.err{background:rgba(255,61,90,.12);border:1px solid rgba(255,61,90,.4);color:#ffb4c0;
  padding:13px 16px;border-radius:14px;font-size:14px}
.note{font-size:13px;color:var(--mut);line-height:1.6}
.badge{display:inline-block;font-size:12px;font-weight:700;padding:5px 12px;border-radius:20px;
  background:rgba(168,85,247,.18);border:1px solid rgba(168,85,247,.45);color:#d8b4fe}
/* style pills */
.pills{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
@media(max-width:520px){.pills{grid-template-columns:repeat(3,1fr)}}
.pill{cursor:pointer}
.pill input{display:none}
.pill .pb{border:1.5px solid var(--line);border-radius:14px;padding:12px 6px;text-align:center;
  background:rgba(255,255,255,.03);transition:all .2s;height:100%}
.pill .pi{font-size:22px;display:block;margin-bottom:4px}
.pill .pt{font-size:12.5px;font-weight:700;display:block}
.pill input:checked+.pb{border-color:var(--acc2);background:rgba(168,85,247,.14);
  box-shadow:0 0 18px rgba(168,85,247,.3)}
/* progress steps */
.steps{display:flex;gap:6px;margin:16px 0}
.step{flex:1;text-align:center;font-size:11.5px;color:var(--mut);font-weight:600}
.step .dot{width:34px;height:34px;border-radius:50%;margin:0 auto 6px;display:flex;
  align-items:center;justify-content:center;background:rgba(255,255,255,.06);
  border:1.5px solid var(--line);font-size:15px;transition:all .3s}
.step.act{color:#fff}
.step.act .dot{border-color:var(--acc2);background:rgba(168,85,247,.2);
  box-shadow:0 0 16px rgba(168,85,247,.45);animation:pulse 1.4s infinite}
.step.done{color:#8f8fa8}
.step.done .dot{border-color:#22c55e;background:rgba(34,197,94,.15)}
@keyframes pulse{50%{transform:scale(1.1)}}
/* feature cards */
.feats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin:20px 0}
@media(max-width:520px){.feats{grid-template-columns:1fr}}
.feat{background:var(--card);border:1px solid var(--line);border-radius:18px;
  padding:18px 14px;text-align:center;backdrop-filter:blur(14px)}
.feat .fi{font-size:30px;display:block;margin-bottom:8px}
.feat b{font-size:14px;display:block;margin-bottom:4px}
.feat span{font-size:12.5px;color:var(--mut);line-height:1.5}
/* hero */
.hero{text-align:center;padding:34px 0 10px}
.hero h1{font-size:40px;line-height:1.12;margin:0 0 12px}
.hero p{color:var(--mut);font-size:16px;line-height:1.6;margin:0 0 6px}
/* stats */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}
.stat{background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:14px;
  padding:12px 8px;text-align:center}
.stat b{font-size:16px;display:block}
.stat span{font-size:11.5px;color:var(--mut)}
/* transcript lines */
.tline{margin:0;padding:10px 4px;border-bottom:1px solid rgba(255,255,255,.05);
  line-height:1.6;font-size:14.5px}
.tline:last-child{border-bottom:0}
.ts{color:#c4b5fd;text-decoration:none;font-size:12.5px;font-weight:600;
  background:rgba(168,85,247,.14);padding:3px 9px;border-radius:8px;margin-right:8px}
details{margin-top:10px}
summary{cursor:pointer;color:#c4b5fd;font-size:14px;font-weight:600}
.chip{display:inline-block;background:rgba(168,85,247,.13);border:1px solid rgba(168,85,247,.35);
  border-radius:20px;padding:5px 13px;font-size:13.5px;margin:3px;cursor:pointer;color:#e6dcff}
.chip:active{background:rgba(168,85,247,.3)}
.rowbtns{display:flex;gap:10px}
.rowbtns>*{margin:0}
.spin{width:44px;height:44px;border-radius:50%;margin:6px auto;
  border:4px solid rgba(168,85,247,.2);border-top-color:#a855f7;animation:rot 1s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
</style>
"""

NAV = """
<div class="nav"><div class="row">
  <div class="logo">🎬 Shorts <span>Factory</span></div>
  <div class="u">{{user}} · <a href="/logout">logout</a></div>
</div><div class="row"><div class="tabs" style="width:100%">
  <a class="tab {{'on' if activetab=='shorts' else ''}}" href="/">🎬 Shorts</a>
  <a class="tab {{'on' if activetab=='transcript' else ''}}" href="/transcript">📝 Transcript</a>
  <a class="tab {{'on' if activetab=='inspect' else ''}}" href="/inspect">🔍 Inspect</a>
</div></div></div>
"""

HOME = CSS + """
<div class="wrap">
""" + NAV + """
  <div class="hero" style="padding-top:10px">
    <h1>Turn any video into<br><span class="grad">10 viral Shorts</span></h1>
    <p class="sub" style="margin-bottom:6px">AI finds the highlights, cuts vertical clips and burns in karaoke subtitles — automatically.</p>
  </div>
  <div class="card">
    <form action="/generate" method="post">
      <label class="lbl" style="margin-top:0">🔗 YouTube link</label>
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <label class="lbl">💬 Subtitle style</label>
      <div class="pills">{{style_pills|safe}}</div>
      <button type="submit">⚡ Generate 10 Shorts</button>
    </form>
    <p class="note" style="text-align:center;margin-bottom:0">🆓 100% free · yt-dlp · faster-whisper · ffmpeg · no API keys</p>
  </div>
</div>
"""

LANDING = CSS + """
<div class="wrap">
  <div class="hero">
    <h1>🎬 Shorts <span class="grad">Factory</span></h1>
    <p>Turn any YouTube video into <b style="color:#fff">10 vertical Shorts</b><br>
    with karaoke subtitles — free, no API keys, yours to keep.</p>
  </div>
  <div class="feats">
    <div class="feat"><span class="fi">⚡</span><b>Auto highlights</b><span>AI scores every moment and cuts the 10 best clips</span></div>
    <div class="feat"><span class="fi">🎤</span><b>Karaoke subtitles</b><span>Word-by-word highlighting in 4 styles — or none</span></div>
    <div class="feat"><span class="fi">📝</span><b>Bonus tools</b><span>Transcript viewer + video inspector built in</span></div>
  </div>
  <div class="card" style="text-align:center">
    <a class="btn" href="/signup">🚀 Create free account</a>
    <a class="btn ghost" href="/login">Log in</a>
  </div>
</div>
"""

SIGNUP = CSS + """
<div class="wrap">
  <div class="hero" style="padding-top:16px"><h1 style="font-size:32px">Create your <span class="grad">account</span></h1></div>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card"><form method="post">
    <input type="text" name="name" required placeholder="Your name">
    <input type="email" name="email" required placeholder="Email">
    <input type="password" name="password" required minlength="6"
           placeholder="Password (min 6 chars)">
    <button type="submit">✨ Sign up free</button>
  </form><p class="note" style="text-align:center">Already have an account? <a href="/login">Log in</a></p></div>
</div>
"""

LOGIN = CSS + """
<div class="wrap">
  <div class="hero" style="padding-top:16px"><h1 style="font-size:32px">Welcome <span class="grad">back</span></h1></div>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card"><form method="post">
    <input type="email" name="email" required placeholder="Email">
    <input type="password" name="password" required placeholder="Password">
    <button type="submit">Log in</button>
  </form><p class="note" style="text-align:center">No account yet? <a href="/signup">Sign up free</a></p></div>
</div>
"""

PROGRESS = CSS + """
<div class="wrap">
  <div class="hero" style="padding-top:16px"><h1 style="font-size:30px">Cooking your <span class="grad">Shorts</span>…</h1></div>
  <div class="card" style="text-align:center">
    <div class="spin"></div>
    <p class="sub" id="msg" style="margin:10px 0 0;font-weight:600;color:#fff">Starting…</p>
    <div class="bar"><div id="fill"></div></div>
    <div class="steps">
      <div class="step" id="s-download"><div class="dot">⬇</div>Download</div>
      <div class="step" id="s-transcribe"><div class="dot">🎤</div>Transcribe</div>
      <div class="step" id="s-highlights"><div class="dot">✨</div>Highlights</div>
      <div class="step" id="s-render"><div class="dot">🎬</div>Render</div>
    </div>
    <p class="note">Takes a few minutes — download + AI transcription + 10 renders.<br>Keep this page open ☕</p>
  </div>
</div>
<script>
const jid="{{jid}}";
const order=["download","transcribe","highlights","render"];
const labels={download:"Downloading video…",transcribe:"AI is transcribing speech…",
  highlights:"Finding the best moments…",render:"Rendering your Shorts…"};
async function tick(){
  const r=await fetch("/status/"+jid); const s=await r.json();
  if(s.stage==="error"){
    document.getElementById("msg").textContent="❌ "+s.error;
    document.querySelector(".spin").style.display="none";return;
  }
  if(s.stage==="done"){location.href="/results/"+jid;return;}
  document.getElementById("msg").textContent=labels[s.stage]||s.stage;
  if(s.total>0)document.getElementById("fill").style.width=
    Math.round(100*s.done/Math.max(s.total,1))+"%";
  const cur=order.indexOf(s.stage);
  order.forEach((k,i)=>{
    const el=document.getElementById("s-"+k);
    el.classList.toggle("act",i===cur);
    el.classList.toggle("done",i<cur);
    if(i<cur)el.querySelector(".dot").textContent="✓";
  });
  setTimeout(tick,2000);
}
tick();
</script>
"""

RESULTS = CSS + """
<div class="wrap">
  <div class="top" style="display:flex;justify-content:space-between;align-items:center">
    <h1 style="margin:0">✅ Your <span class="grad">Shorts</span></h1>
    <span class="u" style="color:var(--mut);font-size:13px">{{user}} · <a href="/">+ new</a></span>
  </div>
  <p class="sub">{{title}}</p>
  <p style="margin:-8px 0 6px"><span class="badge">💬 {{style_label}}</span></p>
  <div class="grid">
  {% for c in clips %}
    <div class="scard">
      <video src="/download/{{jid}}/{{c.file}}" controls preload="metadata" playsinline></video>
      <p class="meta"><b style="color:#fff">#{{loop.index}}</b> · {{c.start}}s–{{c.end}}s<br>{{c.reason}}</p>
      <a class="btn" href="/download/{{jid}}/{{c.file}}" download>⬇ Download</a>
    </div>
  {% endfor %}
  </div>
  <div class="card" style="text-align:center">
    <a class="btn ghost" href="/">🎬 Make more Shorts</a>
  </div>
</div>
"""

GATE_DENIED = CSS + """
<div class="wrap">
  <div class="hero"><h1>⏳ Free access <span class="grad">expired</span></h1>
  <p>Your free generation window has ended.<br>Watch a short ad to unlock another 24 hours — coming soon.</p></div>
  <div class="card" style="text-align:center"><a class="btn ghost" href="/">← Back home</a></div>
</div>
"""

TRANSCRIPT_FORM = CSS + """
<div class="wrap">
""" + NAV + """
  <div class="hero" style="padding-top:6px"><h1 style="font-size:30px">📝 <span class="grad">Transcript</span> viewer</h1>
  <p class="sub">Paste a YouTube link → get its full transcript.</p></div>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card">
    <form action="/transcript" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <button type="submit">📝 Get transcript</button>
    </form>
    <p class="note" style="text-align:center;margin-bottom:0">YouTube captions first (fast) → AI transcription fallback</p>
  </div>
</div>
"""

TRANSCRIPT_RESULT = CSS + """
<div class="wrap">
""" + NAV + """
  <h1 style="font-size:20px;line-height:1.35">{{title}}</h1>
  <p style="margin:0 0 4px"><span class="badge">{{src}}</span>
  <span class="note"> · {{n}} lines</span></p>
  <div class="card"><div class="rowbtns">
    <button id="copybtn">📋 Copy</button>
    <a class="btn ghost" href="/transcript/file/{{token}}.txt" download>⬇ .txt</a>
    <a class="btn ghost" href="/transcript/file/{{token}}.srt" download>⬇ .srt</a>
  </div></div>
  <div class="card" id="tbox" style="padding:10px 18px">
  {% for s in lines %}
    <p class="tline"><a href="#" class="ts" data-t="{{s.t}}">{{s.clock}}</a>{{s.text}}</p>
  {% endfor %}
  </div>
</div>
<script>
document.getElementById("copybtn").onclick=async()=>{
  const t=[...document.querySelectorAll("#tbox .tline")]
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
<div class="wrap">
  <div class="hero"><h1 style="font-size:30px">Transcribing… <span class="grad">⏳</span></h1>
  <p class="sub" id="msg">No YouTube captions on this video — running AI transcription (a few minutes).</p></div>
  <div class="card" style="text-align:center">
    <div class="spin"></div>
    <div class="bar"><div id="fill" style="width:35%"></div></div>
    <p class="note">Keep this page open ☕</p>
  </div>
</div>
<script>
const jid="{{jid}}";
async function tick(){
  const r=await fetch("/tstatus/"+jid); const s=await r.json();
  if(s.stage==="error"){document.getElementById("msg").textContent="❌ "+s.error;return;}
  document.getElementById("msg").textContent=s.msg;
  if(s.stage==="done")location.href="/transcript/view/"+s.token;
  else setTimeout(tick,3000);
}
tick();
</script>
"""

INSPECT_FORM = CSS + """
<div class="wrap">
""" + NAV + """
  <div class="hero" style="padding-top:6px"><h1 style="font-size:30px">🔍 Video <span class="grad">inspector</span></h1>
  <p class="sub">Paste a YouTube link → title, tags, hashtags & description.<br>Handy for competitor research.</p></div>
  {% if err %}<div class="err">{{err}}</div>{% endif %}
  <div class="card">
    <form action="/inspect" method="post">
      <input type="url" name="url" required
             placeholder="https://www.youtube.com/watch?v=...">
      <button type="submit">🔍 Inspect video</button>
    </form>
    <p class="note" style="text-align:center;margin-bottom:0">Reads public metadata only — nothing is downloaded</p>
  </div>
</div>
"""

INSPECT_RESULT = CSS + """
<div class="wrap">
""" + NAV + """
  <div class="card">
    <h1 style="font-size:21px;line-height:1.35;margin-top:0">{{meta.title}}</h1>
    <p class="sub" style="margin-bottom:10px">{{meta.channel}}</p>
    <div class="stats">
      <div class="stat"><b>⏱</b><span>{{meta.duration_str}}</span></div>
      <div class="stat"><b>👁</b><span>{{meta.view_count}}</span></div>
      <div class="stat"><b>👍</b><span>{{meta.like_count}}</span></div>
      <div class="stat"><b>📅</b><span>{{meta.upload_date}}</span></div>
    </div>
    <details><summary>Full description ({{meta.description|length}} chars)</summary>
      <p class="note" style="white-space:pre-wrap">{{meta.description}}</p>
    </details>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <b>🏷 Tags ({{meta.tags|length}})</b>
      <button id="ctags" style="width:auto;padding:9px 16px;margin:0;font-size:13px">Copy all</button></div>
    <p id="tagbox" style="line-height:2.2;margin:0">
    {% for t in meta.tags %}<span class="chip">{{t}}</span> {% else %}
      <span class="note">No tags on this video.</span>{% endfor %}</p>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <b># Hashtags ({{hashtags|length}})</b>
      <button id="chash" style="width:auto;padding:9px 16px;margin:0;font-size:13px">Copy all</button></div>
    <p id="hashbox" style="line-height:2.2;margin:0">
    {% for h in hashtags %}<span class="chip">{{h}}</span> {% else %}
      <span class="note">No hashtags found.</span>{% endfor %}</p>
  </div>
  <p class="note" style="text-align:center"><a href="{{meta.webpage_url}}">Open on YouTube ↗</a></p>
</div>
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
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name,
                                   activetab="transcript")
    url = request.form.get("url", "").strip()
    if not url or "http" not in url:
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name,
                                      activetab="transcript",
                                      err="Please paste a valid YouTube URL.")
    work_dir = os.path.join(BASE_DIR, "transcript_jobs",
                            "fast_" + uuid.uuid4().hex[:8])
    try:
        lines, source = fetch_captions(url, work_dir)
    except RuntimeError as exc:
        return render_template_string(TRANSCRIPT_FORM, user=current_user.name,
                                      activetab="transcript", err=str(exc))
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
        activetab="transcript",
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
        return render_template_string(INSPECT_FORM, user=current_user.name,
                                activetab="inspect")
    url = request.form.get("url", "").strip()
    if not url or "http" not in url:
        return render_template_string(INSPECT_FORM, user=current_user.name,
                                      activetab="inspect",
                                      err="Please paste a valid YouTube URL.")
    try:
        meta = fetch_metadata(url)
    except RuntimeError as exc:
        return render_template_string(INSPECT_FORM, user=current_user.name,
                                      activetab="inspect", err=str(exc))
    hashtags = extract_hashtags(meta["title"], meta["description"])
    return render_template_string(INSPECT_RESULT, user=current_user.name,
                                  activetab="inspect",
                                  meta=meta, hashtags=hashtags)


# --- routes -------------------------------------------------------------------
# Style picker pills for the home page (radio inputs styled as cards).
_STYLE_PILL_META = {
    "karaoke": ("🎤", "Karaoke"),
    "clean": ("✨", "Clean"),
    "pop": ("💥", "Pop"),
    "minimal": ("🌫", "Minimal"),
    "none": ("🚫", "No subs"),
}
STYLE_PILLS = "".join(
    f'<label class="pill"><input type="radio" name="style" value="{k}"'
    f'{" checked" if k == DEFAULT_STYLE else ""}>'
    f'<span class="pb"><span class="pi">{icon}</span>'
    f'<span class="pt">{short}</span></span></label>'
    for k, (icon, short) in _STYLE_PILL_META.items()
)


@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template_string(LANDING)
    return render_template_string(HOME, user=current_user.name,
                                  style_pills=STYLE_PILLS,
                                  activetab="shorts")


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


# --- JSON API (for the native Android client) -------------------------------
# Same session-cookie auth as the web UI (Flask-Login). The Android app
# keeps the cookie jar; all /api/* calls below need a logged-in session.


@login_manager.unauthorized_handler
def _unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "login_required"}), 401
    return redirect(url_for("login"))


def _api_body():
    """Accept JSON or form bodies from API clients."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else request.form


@app.route("/api/signup", methods=["POST"])
def api_signup():
    body = _api_body()
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    pw = str(body.get("password", ""))
    if not name or not email or len(pw) < 6:
        return jsonify({"ok": False,
                        "error": "Fill every field (password min 6 chars)."}), 400
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
        return jsonify({"ok": True, "name": name})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False,
                        "error": "That email is already registered."}), 409


@app.route("/api/login", methods=["POST"])
def api_login():
    body = _api_body()
    email = str(body.get("email", "")).strip().lower()
    pw = str(body.get("password", ""))
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?",
                           (email,)).fetchone()
    if row and check_password_hash(row["password_hash"], pw):
        login_user(User(row))
        return jsonify({"ok": True, "name": row["name"]})
    return jsonify({"ok": False,
                    "error": "Wrong email or password."}), 401


@app.route("/api/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/generate", methods=["POST"])
@login_required
def api_generate():
    if not user_can_generate(current_user):
        return jsonify({"ok": False, "error": "access_expired"}), 403
    body = _api_body()
    url = str(body.get("url", "")).strip()
    style = str(body.get("style", DEFAULT_STYLE))
    if style not in STYLES and style != "none":
        style = DEFAULT_STYLE
    if not url or "http" not in url:
        return jsonify({"ok": False, "error": "Invalid YouTube URL."}), 400
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"user_id": current_user.id, "stage": "starting",
                    "done": 0, "total": 1, "error": None, "result": None,
                    "style": style}
    t = threading.Thread(target=_job_worker, args=(job_id, url, 10, style),
                         daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/status/<job_id>")
@login_required
def api_status(job_id):
    job = _job_or_404(job_id)
    if not job:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "stage": job["stage"], "done": job["done"],
                    "total": job["total"], "error": job["error"]})


@app.route("/api/results/<job_id>")
@login_required
def api_results(job_id):
    job = _job_or_404(job_id)
    if not job or job["stage"] != "done":
        return jsonify({"ok": False, "error": "not_ready"}), 404
    m = job["result"]["manifest"]
    style_key = m.get("style", DEFAULT_STYLE)
    style_label = ("No subtitles" if style_key == "none"
                   else STYLES.get(style_key, {}).get("label", style_key))
    base = request.host_url.rstrip("/")
    return jsonify({
        "ok": True,
        "title": m["title"],
        "style": style_key,
        "style_label": style_label,
        "clips": [
            {"file": c["file"], "start": c["start"], "end": c["end"],
             "reason": c["reason"],
             "url": f"{base}/download/{job_id}/{c['file']}"}
            for c in m["clips"]
        ],
    })


if __name__ == "__main__":
    init_db()
    os.makedirs(JOBS_DIR, exist_ok=True)
    # Hugging Face Spaces expects port 7860; override with PORT env var.
    # Local dev default is also 7860 — set PORT=5000 if you prefer the old one.
    port = int(os.environ.get("PORT", "7860"))
    print(f"Shorts Factory on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
