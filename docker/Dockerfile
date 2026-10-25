# ---- Stage 1: build the web UI ----
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    STORAGE_DIR=/storage \
    PORT=8000

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY --from=frontend /src/dist frontend/dist

ENV PYTHONPATH=/app/backend \
    FRONTEND_DIR=/app/frontend/dist

VOLUME ["/data", "/storage"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4)"

# Exactly ONE worker: Telethon allows only one client per session file, and the
# download engine / scanner / scheduler live in this process's event loop.
CMD ["sh", "-c", "exec uvicorn tglib.app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --timeout-graceful-shutdown 10"]
