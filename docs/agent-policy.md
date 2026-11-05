# Agent Policy

## Purpose
- This file holds the stable, repo-level instructions that should apply to both Codex and Claude-based agents in this workspace.
- Keep transient task status in `docs/agent-context.md`, not here.

## Working Agreement
- Treat this file as long-lived guidance.
- Keep instructions concise, concrete, and actionable.
- Prefer small, reviewable changes.
- Do not overwrite unrelated user changes.
- Do not store task progress or handoff notes here; use `docs/agent-context.md` for that.

## Session Continuity Rule
- Update `docs/agent-context.md` at each milestone and at the end of every task.
- Before closing the workspace or rebuilding the dev container, add a short handoff snapshot.

## Handoff Snapshot Minimum
- Current objective
- What changed
- Next step
- Blockers or risks
- Branch and latest commit hash, if available

## Session Start Rule
- At the start of every new session in this repo, read `AGENTS.md` or `CLAUDE.md` and `docs/agent-context.md` before answering any request, including non-substantive requests.
- If `docs/agent-context.md` is missing, create it and note that continuity context is unavailable.

## Workspace Map
- `weather-brain` owns the Windows Python/FastAPI API, PostgreSQL, archives and analysis.
- Sibling `indoor-sky` owns ESP32-S3 DevKit PlatformIO firmware and Windows/Mac Pi-assisted USB flashing.
- `electric-sky` owns separate outdoor ESP32-S3 CAM firmware.
- Sibling `electric-sea` owns the Raspberry Pi Node.js router and browser interfaces.
- `esp32_ui` is the Next.js/React/TypeScript frontend.
- `b2b-dashboard-demo` is a separate Next.js/React/TypeScript frontend.

## Backend Notes
- Use the repo-local virtual environment at `.venv` for Python work in `esp32_api`.
- The saved workspace should point the Python interpreter at `${workspaceFolder:esp32_api}/.venv/bin/python`.
- `esp32_api/.vscode/settings.json` already keeps the backend interpreter stable for that folder.

## Frontend Notes
- `esp32_ui` and `b2b-dashboard-demo` are standard Node/Next.js projects.
- They generally do not need interpreter-style workspace settings.
- Add workspace-level TypeScript or ESLint overrides only if VS Code misidentifies the frontend tooling in the multi-root workspace.

## Change Hygiene
- Use `apply_patch` for file edits.
- Prefer ASCII unless an existing file clearly uses Unicode.
- Keep comments short and only where the code would otherwise be hard to follow.

