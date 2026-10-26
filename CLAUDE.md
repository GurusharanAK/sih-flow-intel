# CLAUDE.md — Project Memory

Read this file and `PROJECT_STATUS.md` at the start of every session.

## Project
- Name: <name_of_the_project> (placeholder; rename later)
- Event: Smart India Hackathon 2026, Problem Statement 26262
- Title: Integrated AICTE Approval Workflow Automation, Compliance Monitoring and Alert Management System
- Goal: a demonstrable, well-engineered prototype of an approval-operations and compliance-monitoring layer that COMPLEMENTS the official AICTE process.

## Scope (deep, not wide)
A. Configurable approval workflow (stages, transitions, role-gated, blocked-transition explanations)
B. Handoff accountability (send, acknowledge, wait time, escalation)
C. Explainable deterministic delay-risk engine (score 0-100, factors, evidence, bottleneck, next action, override log)
D. Living Compliance Passport (obligations, evidence, verification, reminders shown in-app)
E. Versioned rules: Draft -> Validate -> Preview Impact -> Publish; no silent migration of in-progress applications
F. Deterministic document discrepancy checks (review flags only)
G. Next-best-action queue per role, every item linked and explained

## Stack
- Frontend: React, TypeScript, Vite, Tailwind, shadcn/ui (where practical), Recharts, React Router, React Hook Form + Zod
- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL
- Tests: pytest (backend), Vitest + RTL (critical frontend only)
- Dev: Docker Compose (Postgres + services), `.env.example`, seed/reseed script
- Modular monolith. No microservices, Kafka, Kubernetes, paid APIs, or external infra.

## Repository layout (target)
```
backend/   app/{api,core,models,schemas,services,seed}  alembic/  tests/
frontend/  src/{pages,components,lib,api}
docs/      architecture.md, demo-script.md
```

## Conventions
- Business logic lives ONLY in backend `services/`; routes stay thin; frontend renders what the API returns.
- All displayed metrics come from the database. No hardcoded counters.
- Workflow, deadlines, prerequisites and compliance rules are data (configurable), never constants in code.
- Important transitions run in one DB transaction and write an `AuditEvent`.
- No destructive cascade deletes on audit/approval history; soft-retire instead.
- Consistent error shape: `{ "error": { "code", "message", "details" } }`.
- Paginate and filter list endpoints.
- Small modules, clear names, no unnecessary dependencies.

## Security and honesty constraints
- Prototype auth only (demo credentials, hashed passwords, JWT from env secret). Never describe it as production identity verification.
- RBAC enforced on the backend for every mutating endpoint; frontend hiding is cosmetic.
- Synthetic data only. No real institution or personal data.
- Rule values and timelines are clearly labelled DEMO values. Never present them as official AICTE policy.
- No live AICTE integration claims. No real email/SMS: alerts are in-app only.
- The system never makes statutory approve/reject decisions; humans do. Discrepancies and risk scores are flags and prototype estimates, not judgments or validated predictions.
- Never claim tests passed unless actually run successfully.

## Working rules
- One phase at a time; stop at stable checkpoints.
- Inspect existing code before editing. Do not rewrite working files without reason.
- At end of each phase: update `PROJECT_STATUS.md` (changed files, commands, test results, next step).

## Current phase
**Phase 0 complete (plan + docs). Next: Phase 1 — running skeleton.**
