# PROJECT_STATUS.md

_Last updated: Phase 0 (2026-10-26)_

## Current state
Phase 0 done: environment inspected, plan and project docs written. No application code exists yet.

## Environment findings (Claude sandbox)
- Node v22, Python 3.12.3, git available.
- Docker and PostgreSQL are NOT available in the sandbox, and network is disabled (no `pip`/`npm install`).
- Consequence: the full stack cannot be run or tested inside the sandbox. Code can be written here; running Docker Compose, migrations, and tests must happen on your machine unless a Claude Code / local environment is used.
- Test results: none yet (nothing executed).

## Phase checklist
- [x] Phase 0 — inspect and plan
- [ ] Phase 1 — running skeleton
- [ ] Phase 2 — core workflow
- [ ] Phase 3 — handoffs and delay warnings
- [ ] Phase 4 — compliance and rules
- [ ] Phase 5 — document review and polish
- [ ] Phase 6 — final validation and demo script

## Phase 1 plan (smallest viable)
1. `docker-compose.yml`: `db` (postgres:16) + optional `backend`; frontend run via `npm run dev`.
2. `backend/`: FastAPI app, `core/config.py` (env settings), `core/db.py` (SQLAlchemy engine/session), `GET /health` (includes DB ping), CORS for Vite.
3. Alembic initialised; first migration creates `roles`, `users`, `institutions`, `audit_events` only.
4. `seed` command: 6 demo roles/users (hashed passwords), a few institutions. Reseed command that truncates and reloads.
5. `frontend/`: Vite + React + TS + Tailwind, router with the 10 nav items as placeholder pages, layout shell, and a health-status indicator calling `/health`.
6. `.env.example`, README setup steps.
7. Smoke test: `pytest` for `/health`; confirm `docker compose up`, migration, seed, and frontend load.

Exit criteria: stack starts locally, health check green, seed data present, status documented.

## Assumptions
- Workflow stages, deadlines and rule values are DEMO configuration, not AICTE policy.
- Auth is prototype-grade (JWT + demo passwords).
- Project name is still the placeholder `<name_of_the_project>`.

## Known issues / open questions
- Final project name not chosen.
- Whether to run Postgres via Docker or a local install on the dev machine (default: Docker).

## Next step
Run Phase 1 with: "Proceed to PHASE 1 using CLAUDE.md and PROJECT_STATUS.md. Build only the skeleton described in the Phase 1 plan."

## Update: backend built (stdlib + SQLite)
- `backend/` implements the REST API with server-side RBAC, transactions, audit, risk engine, rules lifecycle, compliance, document checks, action queue.
- 11 unittest tests (scenarios 1-9 + login + human-only decision) executed and passing in the sandbox.
- Not done: PostgreSQL/FastAPI port, Docker, wiring `flow-intel-prototype.html` to the API.
