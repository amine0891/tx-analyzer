FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Chromium is launched only if an allowed article is detected as JS-rendered.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium chromium-driver xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

# PyTorch CPU evite de telecharger les bibliotheques CUDA dans l'image API.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c 'import tomllib; p=tomllib.load(open("pyproject.toml", "rb")); open("/tmp/requirements.txt", "w").write("\n".join(p["project"]["dependencies"]))' \
    && PIP_NO_CACHE_DIR=0 pip install --cache-dir=/root/.cache/pip --timeout 180 --retries 10 'setuptools>=75' \
    && PIP_NO_CACHE_DIR=0 pip install --cache-dir=/root/.cache/pip --timeout 180 --retries 10 -r /tmp/requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY README.md ./
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    DISPLAY=:99

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
