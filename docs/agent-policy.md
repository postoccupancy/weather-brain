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
- Weather Brain Python code is under server/; use the repo-root .venv.
- On Windows the interpreter is .venv/Scripts/python.exe; on macOS/Linux it is .venv/bin/python.
- Select the matching interpreter in VS Code for this checkout; old esp32_api workspace paths are historical.
- Firmware work belongs in sibling indoor-sky; do not recreate device/ here.

## Frontend Notes
- `esp32_ui` and `b2b-dashboard-demo` are standard Node/Next.js projects.
- They generally do not need interpreter-style workspace settings.
- Add workspace-level TypeScript or ESLint overrides only if VS Code misidentifies the frontend tooling in the multi-root workspace.

## Change Hygiene
- Use `apply_patch` for file edits.
- Prefer ASCII unless an existing file clearly uses Unicode.
- Keep comments short and only where the code would otherwise be hard to follow.

## Production Source
- Live deployments, service restarts accompanying updates, and firmware flashing must use approved revisions fetched/pulled from repository remotes, not direct transfers or build artifacts from this development workspace.
- Use a separate clean deployment checkout on the target/build host; verify its revision against the remote before running operational commands. Keep local secrets provisioned separately and ignored.
- Commit/push authorization does not authorize live deployment, restart or firmware flashing.

