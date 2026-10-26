# Flow Intel (SIH 2026 · PS 26262) — AICTE approval flow prototype

Approval workflow, handoff accountability, explainable delay warnings, compliance passport, versioned rules with impact preview, document checks, next-best-action queue, audit log.

> Prototype: synthetic data, DEMO rules and deadlines (not AICTE policy), demo logins, no live AICTE/email/SMS integration. The system never makes statutory decisions; humans do.

## Run locally (no installs needed, Python 3.10+)
```bash
cd backend
python -m flowintel serve        # open http://127.0.0.1:8000/
python -m unittest discover -s tests -v
```
Demo logins (password `demo1234`): institution, regional, scrutiny, evc, sac, admin. Switch with the Role dropdown.

## Deploy on Render
1. Push this folder to a GitHub repo.
2. Render dashboard → New → **Blueprint** → pick the repo (it reads `render.yaml`).
   Or New → Web Service: Root Directory `backend`, Build `pip install -r requirements.txt`, Start `python -m flowintel serve`, env `HOST=0.0.0.0` and `FLOWINTEL_SECRET=<long random string>`.
3. Open the URL Render gives you. Health check: `/api/health`.
Free tier sleeps when idle and may reset the SQLite file; demo data reseeds itself on an empty start. Wake it a minute before presenting.

## Layout
- `backend/` API (stdlib + SQLite), `backend/static/index.html` UI served at `/`, `backend/tests/`
- `standalone/flow-intel-prototype.html` offline single-file demo (own in-browser logic, no API)
- `CLAUDE.md`, `PROJECT_STATUS.md` working notes (earlier plan mentioned FastAPI/PostgreSQL; the built version uses stdlib/SQLite)
