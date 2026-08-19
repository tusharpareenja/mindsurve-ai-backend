# MindSurve API — production image
# Build:  docker build -t mindsurve-ai-backend .
# Run:    docker run --rm -p 8000:8000 --env-file .env mindsurve-ai-backend
# Azure:  set PORT (App Service) and all secrets via App Settings / Key Vault — do not bake .env into the image.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2

WORKDIR /app

# curl = HEALTHCHECK; build-essential rarely needed (psycopg/pillow ship wheels)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from pyproject (avoid requirements.txt editable git line)
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip \
    && pip install .

# Non-root for App Service / Kubernetes
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

# proxy-headers: correct scheme/host behind Azure App Service / reverse proxy
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --workers ${WEB_CONCURRENCY:-2}"]
