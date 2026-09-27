# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Read First
- Read `docs/agent-policy.md` and `docs/agent-context.md` before answering any request in this repo.

## Source of Truth
- `docs/agent-policy.md` contains the shared stable rules for both agents.
- `docs/agent-context.md` contains the current session state and handoff notes.

## Repo Shape
- `esp32_api` is the Python/FastAPI backend.
- `esp32_ui` is the Next.js/React/TypeScript frontend.
- `b2b-dashboard-demo` is a separate Next.js/React/TypeScript frontend.

## Quick Commands
```bash
# Backend setup
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt

# Run API from the backend repo
cd server && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
python -m pytest tests/
python -m pytest tests/test_query_planner.py
python -m pytest tests/test_query_planner.py -k "test_name"
```

## Architecture Summary
- Planning lives under `server/app/planning/`.
- Retrieval lives under `server/app/retrieval/`.
- Execution lives under `server/app/execution/answering/`.
- Framework adapters live under `server/app/frameworks/`.
- API routes live under `server/app/api/`.

## Configuration
- External dependencies are env-driven.
- Copy `.env.example` to `.env` at the repo root before running locally.
- Key variables include the PostgreSQL DATABASE_URL, Ollama model settings, embedding dimensions, and auth tokens.
