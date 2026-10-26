# Flow Intel backend (prototype)

Zero-dependency REST API: Python 3.10+ standard library only (`http.server`, `sqlite3`). No install step.
All data is synthetic, rules are DEMO values (not AICTE policy), auth is demo-grade, and the system never makes statutory decisions.

## Run
```bash
cd backend
python -m flowintel seed     # create / reset synthetic demo data (day 0)
python -m flowintel serve    # http://127.0.0.1:8000  (PORT, HOST, FLOWINTEL_DB, FLOWINTEL_SECRET, FLOWINTEL_CORS env vars)
python -m unittest discover -s tests -v
```
Demo logins (password `demo1234`): `institution`, `regional`, `scrutiny`, `evc`, `sac`, `admin`.
`POST /api/auth/login {"username","password"}` returns a bearer token; send `Authorization: Bearer <token>`.
Set `FLOWINTEL_SECRET` in your environment; the default is random per process.

## Open the UI
After `python -m flowintel serve`, open http://127.0.0.1:8000/ . Use the Role dropdown to switch demo users; every action goes through the API, so forbidden actions return 403 and blocked transitions explain why.

## Host (Render / Railway / similar)
Root directory `backend`, build command empty (or `pip install -r requirements.txt`), start command `python -m flowintel serve`. Env: `HOST=0.0.0.0`, `FLOWINTEL_SECRET=<long random string>`. The host provides `PORT`. Free tiers sleep when idle and may reset the SQLite file; the demo data reseeds itself on empty start.

## Endpoints (all JSON, errors are `{"error":{"code","message","details"}}`)
| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `GET /api/me`, `GET /api/health` |
| Dashboard | `GET /api/dashboard` (scoped to role) |
| Applications | `GET/POST /api/applications` (filters `stage`, `risk`, `page`, `size`), `GET /api/applications/{id}` |
| Workflow | `POST /api/applications/{id}/advance`, `/clarification`, `/clarification/respond`, `/documents`, `/risk-override` |
| Handoffs | `GET /api/handoffs?status=`, `POST /api/applications/{id}/handoff/ack` |
| Delay risk | `GET /api/alerts` (score, factors, evidence, bottleneck, next action) |
| Compliance | `GET /api/obligations`, `POST /api/obligations/{id}/evidence`, `/verify`, `/correction` |
| Rules | `GET /api/rules`, `POST /api/rules/draft`, `/draft/validate`, `/draft/preview`, `/draft/publish` |
| Documents | `GET /api/document-checks` |
| Actions / audit | `GET /api/actions`, `GET /api/audit?ref=` |
| Demo (admin) | `POST /api/demo/advance-day`, `POST /api/demo/reset` |

## Design notes
- RBAC is enforced server-side per endpoint; denied attempts are written to the audit log. Institution users only see their own institution's records.
- Each mutating request runs in one database transaction together with its audit event.
- Audit, handoff and rule history are never deleted; published rules become `retired`, and in-progress applications keep their rule version unless the publisher explicitly chooses `migrate`.
- Business logic is in `flowintel/engine.py`, routes in `flowintel/server.py`, schema and seed in `flowintel/db.py`.

## Known limits
- SQLite and one global lock: fine for a demo, not for production. Swap in PostgreSQL/SQLAlchemy later if needed.
- Demo auth (PBKDF2 hashes, HMAC tokens, no refresh, rate limiting or MFA). No file uploads: documents are metadata only.
- Officers are not restricted by individual case assignment, only by role.
- The UI in `static/index.html` is served by the API at `/` and calls it with relative URLs, so one service hosts everything. The standalone page `flow-intel-prototype.html` is separate and keeps its own in-browser logic.
