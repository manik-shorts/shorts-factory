# Shorts Factory — Docker image for free hosting (Render, Hugging Face Spaces).
#
# Build:  docker build -t shorts-factory .
# Run:    docker run -p 7860:7860 shorts-factory
#
# Notes:
# - python:3.12-slim keeps the image reasonably small.
# - ffmpeg is required by the render pipeline (burning karaoke subtitles,
#   9:16 crops). git is included for pip installs that need it.
# - The Whisper model (~150MB) is NOT baked into the image on purpose:
#   it downloads automatically on the first transcription (see
#   pipeline/transcribe.py). This keeps the image small and the build fast.
# - The app listens on $PORT (Render injects its own; Hugging Face Spaces
#   expects 7860). No PORT is hardcoded here — app.py defaults to 7860
#   when the variable is unset.

FROM python:3.12-slim

# System dependencies: ffmpeg for video work, git for pip.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching when code changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY app.py run.py ./
COPY pipeline/ ./pipeline/
COPY README.md ./

# The app reads PORT from the environment (Render injects its own;
# Hugging Face Spaces expects 7860; app.py defaults to 7860 when unset).
EXPOSE 7860
ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
