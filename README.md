# 🎬 Shorts Factory

**Turn any YouTube video into 10 vertical Shorts with karaoke subtitles — 100% free.**

Paste a YouTube link → the app downloads the video, transcribes it with AI,
automatically finds the 10 best highlight moments, and renders 10 vertical
(1080×1920) Shorts with word-by-word karaoke subtitles burned in.

You own this code. No accounts with third parties, no API keys, no paid
services — everything runs on your own machine.

---

## The free stack

| Stage | Tool | Cost |
|---|---|---|
| Download | [yt-dlp](https://github.com/yt-dlp/yt-dlp) (best ≤1080p MP4) | free |
| Transcription | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `base` model, word timestamps | free (runs on CPU) |
| Highlight picking | Built-in heuristic scorer (no LLM) | free |
| Subtitles | Karaoke `.ass` files, burned in with ffmpeg | free |
| Render | [ffmpeg](https://ffmpeg.org) (9:16 crop → 1080×1920, H.264 + AAC) | free |
| Web UI + accounts | Flask + Flask-Login, SQLite | free |

---

## Setup

**1. Install ffmpeg** (system package — not on PyPI):

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# macOS
brew install ffmpeg

# Windows: download from https://www.gyan.dev/ffmpeg/builds/
```

**2. Install Python packages** (Python 3.10+):

```bash
cd shorts_factory
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**3. First-run note:** the first transcription downloads the ~150 MB Whisper
`base` model automatically (one time, free, cached in
`~/.cache/huggingface/`).

---

## Run it

**Web app** (mobile-friendly — works great from a phone browser):

```bash
python app.py
```

Open **http://localhost:7860** → create an account → paste a YouTube URL →
hit **Generate 10 Shorts** → watch the progress bar → preview and download
your 10 vertical videos from the results page.

> The port comes from the `PORT` environment variable (default `7860`).
> Render injects its own `PORT`; Hugging Face Spaces expects `7860`.
> To use another port locally: `PORT=5000 python app.py`.

**Whisper model:** transcription uses the `WHISPER_MODEL` environment
variable (default `base` = best quality). On low-RAM hosts (e.g.
Render's free tier, 512MB) set `WHISPER_MODEL=tiny` — smaller and
faster, slightly less accurate. Example: `WHISPER_MODEL=tiny python
run.py "<url>"`.

**Command line:**

```bash
python run.py "https://www.youtube.com/watch?v=..." --out ./my_shorts
# → my_shorts/short_01.mp4 … short_10.mp4
```

Options: `--out DIR` (default `./output`), `--count N` (default 10),
`--work DIR` (temp working files, default `<out>/.work`),
`--style STYLE` (default `karaoke`).

**Subtitle styles** (`--style`, or the picker on the web UI):

| Style | Look |
|---|---|
| `karaoke` (default) | Word-by-word highlight: white → yellow sweep, black outline, bottom-center |
| `clean` | Plain white text, black outline, whole line shown, bottom-center |
| `pop` | Big bold yellow text, thick black outline, bottom-center |
| `minimal` | Smaller white text, thin outline, lower-third position |

All four are tuned for 1080×1920 and use the AI word timings —
`karaoke` colors each word as it is spoken; the others show each full
line during its spoken time range.

---

## Deploy to Render (free) 🚀 — recommended

This puts your app on a public website you can open from your phone's
browser — no computer needed. 100% free.

**What you need:** an email address (for GitHub + Render). No credit
card, ever.

### Step-by-step

**1. Put the code on GitHub (free)**
- Create a free account at **github.com/join**
- Create a **new repository** named `shorts-factory` (public or private)
- Upload **all the files** from the `shorts_factory` folder — the
  easiest way: on the repo page click **"uploading an existing file"**
  and drag in `Dockerfile`, `.dockerignore`, `render.yaml`, `app.py`,
  `run.py`, `requirements.txt`, `README.md`, and the whole `pipeline/`
  folder. Then **Commit changes**.

**2. Create a free Render account**
- Go to **render.com** → **"Get Started"** → **sign up with GitHub**
  (one click, uses the account from step 1).

**3. Deploy the app**
- In Render: **New → Blueprint** → connect your `shorts-factory` repo.
- Render reads `render.yaml` and sets everything up automatically:
  Docker runtime, **Free** plan, `WHISPER_MODEL=tiny`.
  (The `tiny` model fits the free tier's 512MB RAM. On a paid/larger
  instance you can set `WHISPER_MODEL=base` for better subtitles.)
- Click **Apply** / **Deploy** and wait ~5–10 minutes for the first build.

**4. Open your app!** 🎉
- Your app is live at `https://shorts-factory.onrender.com`
- Open it on your phone → **create an account** → paste a YouTube link
  → get your 10 Shorts.

### Updating the app later

Push new files to the GitHub repo → Render rebuilds and redeploys
automatically.

### Free-tier limits (honest notes)

- **512MB RAM → `tiny` Whisper model:** subtitles are slightly less
  accurate than the `base` model used on a laptop. Good enough for
  clear speech; upgrade the Render plan (or run locally) for `base`.
- **Speed:** free CPU is slow. A 4-minute video can take ~15–25 minutes
  end-to-end (transcription is the slow part). The progress bar keeps
  you posted — keep the page open.
- **Sleep:** the service sleeps after 15 minutes with no visitors; the
  first visit after sleep takes ~1 minute to wake up.
- **750 hours/month:** one always-on service fits comfortably inside
  the free allowance.
- **YouTube downloads:** YouTube sometimes blocks datacenter IPs ("sign
  in to confirm you're not a bot"). The app retries automatically with
  a fallback; if a download fails, try again later.
- **User accounts reset on rebuild:** the SQLite database lives inside
  the container, so accounts are lost when Render rebuilds/redeploys —
  sign up again after an update. A hosted database is the future fix.

---

## Deploy to Hugging Face Spaces (free) 🌐

> ⚠️ **Update (Oct 2026):** Hugging Face now requires a **paid PRO plan
> ($9/month)** to host Docker Spaces — the free path below no longer
> works for this app. **Use Render (above) for free hosting.** This
> section is kept for reference if you ever get HF PRO.

This puts your app on a public website you can open from your phone's
browser — no computer needed. 100% free.

**What you need:** just an email address. No credit card, ever.

### Step-by-step

**1. Create a free Hugging Face account**
- Go to **huggingface.co/join** on your phone or computer
- Sign up with your email (or continue with Google)

**2. Create a new Space**
- Go to **huggingface.co/spaces** and click **"Create new Space"**
- **Space name:** `shorts-factory` (or anything you like)
- **License:** pick any (e.g. MIT)
- **Space SDK:** choose **Docker** ← important!
- **Space hardware:** leave as **CPU basic** (free)
- **Visibility:** Public or Private — your choice
- Click **"Create Space"**

**3. Upload the app files**
- On your new Space page, click the **"Files"** tab → **"Add file"** →
  **"Upload files"**
- Upload **exactly these files and folders** from the `shorts_factory`
  folder on your computer:
  - `Dockerfile`
  - `.dockerignore`
  - `app.py`
  - `run.py`
  - `requirements.txt`
  - `README.md`
  - the whole `pipeline/` folder (all 8 files inside it)
- Click **"Commit changes"** (or "Upload")

**4. Wait for the build** ⏳
- Hugging Face now builds your app automatically — this takes about
  **5–10 minutes** the first time. You can watch the progress on the
  Space page (it shows log lines like "Building…" then "Running…").
- If the build fails, click the failed build to see the error log —
  the most common cause is a missing file from step 3.

**5. Open your app!** 🎉
- When the status shows **"Running"**, your app is live at an address
  like `https://YOUR-USERNAME-shorts-factory.hf.space`
- Open it on your phone → **create an account** → paste a YouTube link
  → get your 10 Shorts.

### Updating the app later

Change a file → upload the new version to the Space's **Files** tab →
commit → Hugging Face rebuilds automatically (~2–5 min).

### Free-tier limits (honest notes)

- **User accounts reset on rebuild:** the free tier does not keep the
  SQLite database when the Space is rebuilt or restarted from scratch —
  you (and your users) may need to sign up again after an update. This
  is acceptable for now; a future upgrade can use a hosted database.
- **Speed:** the free CPU is slower than a laptop. A 4-minute video can
  take ~10–20 minutes to process (transcription is the slow part). The
  progress bar keeps you posted — keep the page open.
- **Sleep:** if nobody opens the Space for a while, it sleeps; the
  first visit after sleep takes ~1 minute to wake up.
- **YouTube downloads:** YouTube sometimes blocks datacenter IPs ("sign
  in to confirm you're not a bot"). The app retries automatically with a
  fallback; if a download fails, try again later.

---

## How the pipeline works

```
YouTube URL
   │  pipeline/download.py   – yt-dlp, best video ≤1080p + audio → source.mp4
   ▼
pipeline/transcribe.py  – faster-whisper "base", word_timestamps=True
                          → transcript.json (cached; re-runs skip this)
   ▼
pipeline/highlights.py  – heuristic scoring, NO paid LLM:
                          questions/hooks, exclamations, numbers,
                          superlatives, emotional words, speech density,
                          opening bonus → top 10 non-overlapping windows,
                          max 3 per quarter so shorts spread across the video
   ▼  (for each of the 10 windows)
pipeline/subtitles.py   – word timings → styled .ass (karaoke/clean/pop/minimal)
                          (white → yellow word-by-word, black outline,
                          bottom-center, tuned for 1080×1920)
   ▼
pipeline/render.py      – ffmpeg: center-crop 9:16 (slight upward bias),
                          scale 1080×1920, burn subtitles, AAC 128k.
                          Exactly one video + one audio stream.
```

`pipeline/runner.py` orchestrates the stages; `run.py` (CLI) and `app.py`
(web) both call it, so behaviour is identical in both.

---

## Accounts & access control

- **Signup:** name, email, password. Passwords are hashed with
  `werkzeug.security` (PBKDF2) — never stored in plain text.
- **Storage:** local SQLite file `shorts_factory.db`, table `users`
  (`id, name, email UNIQUE, password_hash, created_at`).
- **Sessions:** Flask-Login. Anonymous visitors are redirected to the login
  page; the URL-paste/generate feature requires login.
- **The one gate:** `user_can_generate(user)` in `app.py`. It currently
  returns `True` for any logged-in user. **All future access rules go here**
  — e.g. "watch N ads → 24 h free access" — so the route code never changes.
  No ads are built in v1.

> 🔑 The Flask secret key is auto-generated on first run and stored in
> `instance/secret.key` (git-ignored pattern — do not share it).

---

## Project structure

```
shorts_factory/
├── README.md            – you are here
├── requirements.txt     – pinned, tested-together versions
├── Dockerfile           – container image for free hosting (Render, HF Spaces)
├── .dockerignore        – keeps the Docker build small
├── render.yaml          – Render Blueprint: one-click free deploy
├── app.py               – Flask web UI + auth + background jobs
├── run.py               – CLI: python run.py <url> [--out dir]
└── pipeline/
    ├── __init__.py
    ├── runner.py        – orchestrates the 5 stages
    ├── download.py      – yt-dlp download (≤1080p mp4)
    ├── transcribe.py    – faster-whisper, word timestamps, JSON cache
    ├── highlights.py    – heuristic highlight scoring
    ├── subtitles.py     – styled .ass generation (karaoke/clean/pop/minimal, 1080×1920)
    └── render.py        – ffmpeg 9:16 crop + subtitle burn-in
```

Runtime files (created automatically, not in git): `shorts_factory.db`,
`jobs/`, `instance/secret.key`, `output/`, `.work/`.

---

## Troubleshooting

- **"Download failed" / HTTP 403 / "Sign in to confirm you're not a bot":**
  YouTube throttles some IPs and datacenters aggressively. The app
  automatically retries with YouTube's Android player client, which
  usually still works (it may top out at 360p on such networks — the
  Shorts are still rendered at full 1080×1920). If both attempts fail,
  try again later or run the app from a different network. The error
  message shows yt-dlp's last lines.
- **Slow first transcription:** the Whisper model downloads once (~150 MB),
  then it is cached. CPU transcription runs ~5–10× faster than realtime on
  a modern laptop.
- **Subtitles show a fallback font:** install a font like Arial/DejaVu
  (`sudo apt install fonts-dejavu`) — libass substitutes automatically.
- **Only N < 10 shorts produced:** very short or nearly silent videos have
  fewer distinct moments; the pipeline returns what it can find.
- **Port busy / change port:** set the `PORT` environment variable,
  e.g. `PORT=5000 python app.py`.

---

## Ownership

Built for **Manik**. He owns this code and may modify, host, or
commercialize it freely. The stack is 100% free and open-source; no vendor
lock-in, no API keys to rotate, no usage bills.
