# TG Library

Self-hosted web app that downloads books and documents from Telegram chats you
are a member of, deduplicates them, and resumes interrupted downloads.

> Full Arabic/English setup guide arrives with the hardening phase.

## Quick start

```sh
cp .env.example .env      # fill TG_API_ID, TG_API_HASH, APP_PASSWORD, STORAGE_HOST_PATH
docker compose up -d --build
```

Open http://127.0.0.1:8000.

## Development

```sh
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt
.venv/Scripts/python -m pytest
cd frontend && npm install && npm run dev
```
