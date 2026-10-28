"""Zero-dependency REST API (stdlib http.server + sqlite3) for the Flow Intel prototype.
Prototype auth only: demo users, PBKDF2 password hashes, HMAC-signed bearer tokens. Not production identity verification."""
import base64, hashlib, hmac, json, os, re, secrets, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from . import db as D, engine as E
from .db import APP_TYPES, DOCS, PRODUCER, ROLES, STAGES, required_items

SECRET = os.environ.get("FLOWINTEL_SECRET") or secrets.token_hex(16)  # dev default is per-process random; set it in .env
ALLOW_ORIGIN = os.environ.get("FLOWINTEL_CORS", "*")
LOCK = threading.Lock()
ROUTES = []
INST, REG, SCR, EVC, SAC, ADM = ROLES
VERIFIERS, OVERRIDERS = (REG, ADM), (REG, ADM)


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def sign(payload):
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return b + "." + hmac.new(SECRET.encode(), b.encode(), hashlib.sha256).hexdigest()


def unsign(tok):
    try:
        b, sig = tok.split(".")
        if not hmac.compare_digest(sig, hmac.new(SECRET.encode(), b.encode(), hashlib.sha256).hexdigest()):
            return None
        p = json.loads(base64.urlsafe_b64decode(b))
        return p if p["exp"] > time.time() else None
    except Exception:
        return None


def route(method, pattern, roles=None, auth=True):
    rx = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, roles, auth, fn))
        return fn
    return deco


class Req:
    def __init__(self, db, user, body, query):
        self.db, self.user, self.body, self.query = db, user, body, query
        self.ctx = E.load_ctx(db)
        self.day = self.ctx["day"]

    def audit(self, action, ref="", detail=""):
        self.db.execute("INSERT INTO audit(day,actor,role,action,ref,detail) VALUES(?,?,?,?,?,?)", (self.day, self.user["username"], self.user["role"], action, ref, detail))

    def denied(self, why):
        self.audit("DENIED", "", why)
        self.db.commit()  # keep denial in the audit trail
        raise ApiError(403, "FORBIDDEN", why)

    def need(self, *roles, why=None):
        if self.user["role"] not in roles:
            self.denied(why or f"{self.user['role']} may not perform this action (allowed: {', '.join(roles)})")

    def app(self, aid):
        a = next((x for x in self.ctx["apps"] if x["id"] == aid), None)
        if not a or (self.user["role"] == INST and a["iid"] != self.user["iid"]):
            raise ApiError(404, "NOT_FOUND", f"Application {aid} not found")
        return a

    def scoped_apps(self):
        return [a for a in self.ctx["apps"] if self.user["role"] != INST or a["iid"] == self.user["iid"]]

    def obligation(self, oid):
        o = self.db.execute("SELECT * FROM obligations WHERE id=?", (oid,)).fetchone()
        if not o or (self.user["role"] == INST and o["iid"] != self.user["iid"]):
            raise ApiError(404, "NOT_FOUND", f"Obligation {oid} not found")
        return dict(o)

    def field(self, key, kind=str, required=True):
        v = (self.body or {}).get(key)
        if v is None or (kind is str and not str(v).strip()):
            if required:
                raise ApiError(422, "VALIDATION_ERROR", f"'{key}' is required")
            return None
        if not isinstance(v, kind):
            raise ApiError(422, "VALIDATION_ERROR", f"'{key}' must be {kind.__name__}")
        return v.strip() if kind is str else v


def page(req, items):
    try:
        p, s = max(1, int(req.query.get("page", 1))), min(100, max(1, int(req.query.get("size", 25))))
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "page and size must be integers")
    return {"total": len(items), "page": p, "size": s, "items": items[(p - 1) * s:p * s]}


def app_summary(a, ctx):
    r = E.risk(a, ctx)
    return {"id": a["id"], "institution": ctx["inst"][a["iid"]]["name"], "institution_id": a["iid"], "type": a["type"], "stage": STAGES[a["stage"]][0],
            "owner_role": E.owner(a), "days_in_stage": ctx["day"] - a["entered"] if a["stage"] < 6 else None,
            "risk_score": r["score"], "risk_category": E.category(a, ctx), "overridden": bool(a["ovr"]), "rule_version": a["rv"],
            "handoff_acknowledged": None if not a["ho"] or a["stage"] >= 6 else a["ho"]["ack"] is not None}


def handoff_view(h, ctx):
    w = ctx["day"] - h["sent"] if h["ack"] is None else h["ack"] - h["sent"]
    return {**h, "missing": json.loads(h["missing"] or "[]"), "waiting_days": w, "acknowledged": h["ack"] is not None,
            "escalation": "Escalate to Regional Officer" if h["ack"] is None and w >= 3 else None}


def obligation_view(o, day):
    return {**o, "display_status": E.ob_status(o, day)}


# ---------- public / auth ----------
@route("GET", "/api/health", auth=False)
def health(req):
    return {"status": "ok", "db": req.db.execute("SELECT 1").fetchone()[0] == 1, "disclaimer": "Prototype: synthetic data, demo rules, demo auth"}


@route("POST", "/api/auth/login", auth=False)
def login(req):
    u = req.db.execute("SELECT * FROM users WHERE username=?", (req.field("username"),)).fetchone()
    if not u or not D.check_pw(req.field("password"), u["pw"]):
        raise ApiError(401, "BAD_CREDENTIALS", "Invalid username or password")
    return {"token": sign({"u": u["id"], "exp": time.time() + 8 * 3600}), "user": {"username": u["username"], "role": u["role"], "institution_id": u["iid"]},
            "notice": "Prototype authentication with demo credentials"}


@route("GET", "/api/me")
def me(req):
    return {"username": req.user["username"], "role": req.user["role"], "institution_id": req.user["iid"]}


# ---------- dashboard ----------
@route("GET", "/api/dashboard")
def dashboard(req):
    ctx, apps = req.ctx, [a for a in req.scoped_apps()]
    live = [a for a in apps if a["stage"] < 6]
    obs = [dict(o) for o in req.db.execute("SELECT * FROM obligations") if req.user["role"] != INST or o["iid"] == req.user["iid"]]
    st = [E.ob_status(o, ctx["day"]) for o in obs]
    ids = {a["id"] for a in apps}
    recent = [dict(r) for r in req.db.execute("SELECT day,actor,role,action,ref,detail FROM audit ORDER BY id DESC LIMIT 40")]
    return {"day": ctx["day"], "total_applications": len(apps),
            "by_stage": {s[0]: sum(1 for a in apps if a["stage"] == i) for i, s in enumerate(STAGES)},
            "pending_handoffs": sum(1 for a in live if a["ho"]),
            "unacknowledged_handoffs": sum(1 for a in live if a["ho"] and a["ho"]["ack"] is None),
            "high_risk_applications": sum(1 for a in live if E.category(a, ctx) == "High"),
            "obligations": {"overdue": sum(1 for s in st if "overdue" in s.lower() or s == "Overdue"), "due_soon": st.count("Due soon"),
                            "awaiting_verification": st.count("Awaiting verification"), "verified": st.count("Verified"), "total": len(obs)},
            "recent_activity": [r for r in recent if r["ref"] in ids or r["ref"] == "" or req.user["role"] != INST][:5],
            "next_actions": E.next_actions(req.user, req.db, ctx)[:8]}


# ---------- applications ----------
@route("GET", "/api/applications")
def list_apps(req):
    items = [app_summary(a, req.ctx) for a in req.scoped_apps()]
    q = req.query
    if q.get("stage"):
        items = [i for i in items if i["stage"] == q["stage"]]
    if q.get("risk"):
        items = [i for i in items if i["risk_category"] == q["risk"]]
    if q.get("institution_id"):
        items = [i for i in items if str(i["institution_id"]) == q["institution_id"]]
    return page(req, items)


@route("POST", "/api/applications", roles=(INST,))
def create_app(req):
    typ = req.field("type")
    if typ not in APP_TYPES:
        raise ApiError(422, "VALIDATION_ERROR", f"type must be one of {APP_TYPES}")
    n = req.db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] + 1
    aid, iid = f"APP-26{n:02d}", req.user["iid"]
    while req.db.execute("SELECT 1 FROM applications WHERE id=?", (aid,)).fetchone():
        n += 1
        aid = f"APP-26{n:02d}"
    rv = max(r[0] for r in req.db.execute("SELECT version FROM rules WHERE status='published'"))
    req.db.execute("INSERT INTO applications VALUES(?,?,?,0,?,?,?,NULL,NULL)", (aid, iid, typ, req.day, json.dumps(["Application form"]), rv))
    inst = req.ctx["inst"][iid]
    req.db.execute("INSERT INTO documents(app_id,name,expiry,code,inst_name,issuer) VALUES(?,?,?,?,?,?)", (aid, "Application form", None, inst["code"], inst["name"], "Applicant"))
    sla = json.loads(req.db.execute("SELECT sla FROM rules WHERE version=?", (rv,)).fetchone()[0])
    req.db.execute("INSERT INTO handoffs(app_id,from_role,to_role,from_stage,to_stage,sent,ack,ack_by,expected_by,missing) VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                   (aid, INST, REG, "Applicant", "Submitted", req.day, req.day + sla["Submitted"], json.dumps([x for x in required_items(0, typ, req.ctx["docs"][rv]) if x != "Application form"])))
    req.audit("application.created", aid, f"{typ}; routed to {REG} queue")
    return 201, {"id": aid, "stage": "Submitted", "owner_role": REG}


def clarification(db, a):
    if a["clar_since"] is None:
        return None
    r = db.execute("SELECT detail FROM audit WHERE ref=? AND action='clarification.requested' ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
    return {"since": a["clar_since"], "reason": r["detail"] if r else ""}


def detail(req, a):
    ctx, db = req.ctx, req.db
    hs = [handoff_view(dict(h), ctx) for h in db.execute("SELECT * FROM handoffs WHERE app_id=? ORDER BY id", (a["id"],))]
    obs = [obligation_view(dict(o), ctx["day"]) for o in db.execute("SELECT * FROM obligations WHERE iid=?", (a["iid"],))]
    hist = [dict(r) for r in db.execute("SELECT day,actor,role,action,detail FROM audit WHERE ref=? ORDER BY id", (a["id"],))]
    return {**app_summary(a, ctx), "prerequisites_missing": E.missing(a, ctx) if a["stage"] < 6 else [], "blockers": E.blockers(a, ctx), "required_documents": ctx["docs"][a["rv"]].get(a["type"], []),
            "missing_detail": [{"item": x, "provider_role": PRODUCER.get(x, ROLES[0]), "institution_provided": x in DOCS} for x in (E.missing(a, ctx) if a["stage"] < 6 else [])],
            "clarification": clarification(db, a), "risk": E.risk(a, ctx),
            "override": a["ovr"], "clarification_pending": a["clar_since"] is not None, "documents": a["docs"], "document_findings": E.findings(a, ctx),
            "current_handoff": hs[-1] if hs else None, "handoff_history": hs, "obligations": obs, "history": hist,
            "configured_stage_days": ctx["slas"][a["rv"]][STAGES[a["stage"]][0]]}


@route("GET", "/api/applications/{aid}")
def get_app(req, aid):
    return detail(req, req.app(aid))


@route("POST", "/api/applications/{aid}/advance")
def advance(req, aid):
    a = req.app(aid)
    if a["stage"] >= 6:
        raise ApiError(409, "ALREADY_CLOSED", "A decision has already been recorded")
    if req.user["role"] != E.owner(a):
        req.denied(f"{req.user['role']} cannot move {aid}; stage owner is {E.owner(a)}")
    b = E.blockers(a, req.ctx)
    if b:
        req.audit("transition.blocked", aid, "; ".join(b))
        req.db.commit()
        raise ApiError(409, "TRANSITION_BLOCKED", "Transition blocked: " + "; ".join(b), b)
    frm, new = STAGES[a["stage"]], a["stage"] + 1
    req.db.execute("UPDATE applications SET stage=?,entered=? WHERE id=?", (new, req.day, aid))
    if new < 6:
        nxt = STAGES[new]
        sla = req.ctx["slas"][a["rv"]][nxt[0]]
        req.db.execute("INSERT INTO handoffs(app_id,from_role,to_role,from_stage,to_stage,sent,ack,ack_by,expected_by,missing) VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                       (aid, frm[1], nxt[1], frm[0], nxt[0], req.day, req.day + sla, json.dumps([x for x in required_items(new, a["type"], req.ctx["docs"][a["rv"]]) if x not in a["have"]])))
        req.audit("stage.advanced", aid, f"{frm[0]} -> {nxt[0]}; handoff sent to {nxt[1]}")
    else:
        req.audit("decision.recorded", aid, "Authorised decision recorded by a human officer (system never decides)")
    return {"id": aid, "stage": STAGES[new][0]}


@route("POST", "/api/applications/{aid}/handoff/ack")
def ack(req, aid):
    a = req.app(aid)
    h = a["ho"]
    if not h or a["stage"] >= 6:
        raise ApiError(409, "NO_HANDOFF", "No open handoff")
    if req.user["role"] != h["to_role"]:
        req.denied(f"{req.user['role']} is not the handoff recipient ({h['to_role']})")
    if h["ack"] is not None:
        raise ApiError(409, "ALREADY_ACKNOWLEDGED", "Handoff already acknowledged")
    req.db.execute("UPDATE handoffs SET ack=?,ack_by=? WHERE id=?", (req.day, req.user["username"], h["id"]))
    req.audit("handoff.acknowledged", aid, f"{h['from_role']} -> {h['to_role']} acknowledged after {req.day - h['sent']}d")
    return {"id": aid, "acknowledged_day": req.day}


@route("POST", "/api/applications/{aid}/clarification")
def clar(req, aid):
    a = req.app(aid)
    if req.user["role"] != E.owner(a):
        req.denied("only the current stage owner may request clarification")
    why = req.field("reason")  # what the institution must clarify; internal items are not institution clarifications
    if a["clar_since"] is not None:
        raise ApiError(409, "ALREADY_PENDING", "A clarification is already pending")
    req.db.execute("UPDATE applications SET clar_since=? WHERE id=?", (req.day, aid))
    req.audit("clarification.requested", aid, why)
    return {"id": aid, "clarification_pending": True}


@route("POST", "/api/applications/{aid}/clarification/withdraw")
def clar_withdraw(req, aid):
    a = req.app(aid)
    if req.user["role"] != E.owner(a):
        req.denied("only the current stage owner may withdraw a clarification")
    if a["clar_since"] is None:
        raise ApiError(409, "NO_CLARIFICATION", "No clarification is pending")
    req.db.execute("UPDATE applications SET clar_since=NULL WHERE id=?", (aid,))
    req.audit("clarification.withdrawn", aid, req.field("reason", required=False) or "")
    return {"id": aid, "clarification_pending": False}


@route("POST", "/api/applications/{aid}/internal-items")
def record_item(req, aid):
    a = req.app(aid)
    item = req.field("item")
    if item not in PRODUCER:
        raise ApiError(422, "VALIDATION_ERROR", f"'{item}' is not an internal item; institution documents are uploaded by the institution")
    if req.user["role"] != PRODUCER[item]:
        req.denied(f"{item} can only be recorded by {PRODUCER[item]}")
    if item in a["have"]:
        raise ApiError(409, "ALREADY_PRESENT", f"{item} is already recorded")
    if item not in E.missing(a, req.ctx):
        raise ApiError(409, "NOT_REQUIRED", f"{item} is not required at {STAGES[a['stage']][0]}")
    req.db.execute("UPDATE applications SET have=? WHERE id=?", (json.dumps(a["have"] + [item]), aid))
    req.audit("item.recorded", aid, item)
    return 201, {"id": aid, "recorded": item}


@route("POST", "/api/applications/{aid}/return")
def send_back(req, aid):
    a = req.app(aid)
    if a["stage"] >= 6:
        raise ApiError(409, "ALREADY_CLOSED", "A decision has already been recorded")
    if req.user["role"] != E.owner(a):
        req.denied(f"{req.user['role']} cannot return {aid}; stage owner is {E.owner(a)}")
    if a["stage"] == 0:
        raise ApiError(409, "FIRST_STAGE", "Submitted is the first stage; request a clarification from the institution instead")
    why = req.field("reason")
    frm, prev = STAGES[a["stage"]], a["stage"] - 1
    to = STAGES[prev]
    req.db.execute("UPDATE applications SET stage=?,entered=? WHERE id=?", (prev, req.day, aid))
    req.db.execute("INSERT INTO handoffs(app_id,from_role,to_role,from_stage,to_stage,sent,ack,ack_by,expected_by,missing) VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                   (aid, frm[1], to[1], frm[0], to[0], req.day, req.day + req.ctx["slas"][a["rv"]][to[0]],
                    json.dumps([x for x in required_items(prev, a["type"], req.ctx["docs"][a["rv"]]) if x not in a["have"]])))
    req.audit("stage.returned", aid, f"{frm[0]} -> {to[0]} (returned to {to[1]}): {why}")
    return {"id": aid, "stage": to[0], "owner_role": to[1]}


@route("POST", "/api/applications/{aid}/clarification/respond", roles=(INST,))
def clar_resp(req, aid):
    req.app(aid)
    req.db.execute("UPDATE applications SET clar_since=NULL WHERE id=?", (aid,))
    req.audit("clarification.answered", aid, "")
    return {"id": aid, "clarification_pending": False}


@route("POST", "/api/applications/{aid}/documents", roles=(INST,))
def upload(req, aid):
    a = req.app(aid)
    item = req.field("item")
    if item not in a["have"] and item not in DOCS and item not in {x for s in STAGES for x in s[2]}:
        raise ApiError(422, "VALIDATION_ERROR", f"Unknown prerequisite '{item}'")
    if item in a["have"]:
        raise ApiError(409, "ALREADY_PRESENT", f"{item} already on file")
    if item not in DOCS:
        req.denied(f"{item} is produced internally, not uploaded by an institution")
    inst = req.ctx["inst"][a["iid"]]
    req.db.execute("UPDATE applications SET have=? WHERE id=?", (json.dumps(a["have"] + [item]), aid))
    req.db.execute("INSERT INTO documents(app_id,name,expiry,code,inst_name,issuer) VALUES(?,?,?,?,?,?)",
                   (aid, item, req.body.get("expiry_day"), inst["code"], inst["name"], req.body.get("issuer", "Applicant")))
    req.audit("document.uploaded", aid, item)
    return 201, {"id": aid, "uploaded": item}


@route("POST", "/api/applications/{aid}/risk-override", roles=OVERRIDERS)
def override(req, aid):
    a = req.app(aid)
    c, why = req.field("category"), req.field("reason")
    if c not in ("Low", "Medium", "High"):
        raise ApiError(422, "VALIDATION_ERROR", "category must be Low, Medium or High")
    ov = {"category": c, "reason": why, "by": req.user["username"], "role": req.user["role"], "day": req.day}
    req.db.execute("UPDATE applications SET ovr=? WHERE id=?", (json.dumps(ov), aid))
    req.audit("risk.overridden", aid, f"{c}: {why}")
    return ov


# ---------- handoffs / alerts ----------
@route("GET", "/api/handoffs")
def handoffs(req):
    out = []
    for a in req.scoped_apps():
        if a["stage"] < 6 and a["ho"]:
            out.append({"application_id": a["id"], **handoff_view(a["ho"], req.ctx)})
    s = req.query.get("status")
    if s:
        out = [h for h in out if h["acknowledged"] == (s == "acknowledged")]
    return page(req, sorted(out, key=lambda h: -h["waiting_days"]))


@route("GET", "/api/alerts")
def alerts(req):
    rows = []
    for a in req.scoped_apps():
        if a["stage"] < 6:
            rows.append({**app_summary(a, req.ctx), "risk": E.risk(a, req.ctx), "override": a["ovr"]})
    rows.sort(key=lambda r: -r["risk_score"])
    if req.query.get("risk"):
        rows = [r for r in rows if r["risk_category"] == req.query["risk"]]
    return page(req, rows)


# ---------- compliance ----------
@route("GET", "/api/obligations")
def obligations(req):
    rows = []
    for o in req.db.execute("SELECT * FROM obligations ORDER BY due"):
        if req.user["role"] == INST and o["iid"] != req.user["iid"]:
            continue
        v = obligation_view(dict(o), req.day)
        v["institution"] = req.ctx["inst"][o["iid"]]["name"]
        v["history"] = [dict(e) for e in req.db.execute("SELECT day,actor,event FROM obligation_events WHERE ob_id=? ORDER BY id", (o["id"],))]
        v["escalation"] = "Escalate to Regional Officer" if v["display_status"].endswith("overdue)") or v["display_status"] == "Overdue" else None
        rows.append(v)
    if req.query.get("status"):
        rows = [r for r in rows if r["display_status"] == req.query["status"]]
    return page(req, rows)


def ob_event(req, oid, event):
    req.db.execute("INSERT INTO obligation_events(ob_id,day,actor,event) VALUES(?,?,?,?)", (oid, req.day, req.user["username"], event))
    req.audit("obligation." + event.split(":")[0].lower().replace(" ", "_"), oid, event)


@route("POST", "/api/obligations/{oid}/evidence", roles=(INST,))
def evidence(req, oid):
    o = req.obligation(oid)
    if o["status"] not in ("pending", "correction"):
        raise ApiError(409, "INVALID_STATE", f"Obligation is already {o['status']}")
    req.db.execute("UPDATE obligations SET status='submitted',note='' WHERE id=?", (oid,))
    ob_event(req, oid, "Evidence submitted: " + (req.field("evidence", required=False) or "synthetic evidence"))
    return {"id": oid, "status": "submitted"}


@route("POST", "/api/obligations/{oid}/verify", roles=VERIFIERS)
def verify(req, oid):
    o = req.obligation(oid)
    if o["status"] != "submitted":
        raise ApiError(409, "INVALID_STATE", "Only submitted evidence can be verified")
    req.db.execute("UPDATE obligations SET status='verified',ver_by=?,ver_at=? WHERE id=?", (req.user["role"], req.day, oid))
    ob_event(req, oid, "Verified")
    return {"id": oid, "status": "verified"}


@route("POST", "/api/obligations/{oid}/correction", roles=VERIFIERS)
def correction(req, oid):
    o = req.obligation(oid)
    if o["status"] != "submitted":
        raise ApiError(409, "INVALID_STATE", "Only submitted evidence can be returned for correction")
    why = req.field("reason")
    req.db.execute("UPDATE obligations SET status='correction',note=? WHERE id=?", (why, oid))
    ob_event(req, oid, "Correction requested: " + why)
    return {"id": oid, "status": "correction", "reason": why}


# ---------- rules ----------
def rule_view(r):
    return {"version": r["version"], "status": r["status"], "sla": json.loads(r["sla"]), "docs": json.loads(r["docs"]) if r["docs"] else None, "by": r["by_user"], "day": r["at"], "note": r["note"],
            "migration": r["migration"], "validation_error": r["err"], "preview": json.loads(r["preview"]) if r["preview"] else None}


@route("GET", "/api/rules")
def rules(req):
    return {"rule_id": "DEMO-RULES-01", "rule_types": ["Stage time limits", "Required documents per application type"], "label": "DEMO values, not AICTE policy", "versions": [rule_view(r) for r in req.db.execute("SELECT * FROM rules ORDER BY version")]}


def open_draft(req):
    d = req.db.execute("SELECT * FROM rules WHERE status IN ('draft','validated','previewed')").fetchone()
    if not d:
        raise ApiError(409, "NO_DRAFT", "Create a draft first")
    return d


def cur_rule(req):
    return req.db.execute("SELECT * FROM rules WHERE status='published' ORDER BY version DESC LIMIT 1").fetchone()


def norm(docs):
    return {t: sorted(v) for t, v in docs.items()}


@route("POST", "/api/rules/draft", roles=(ADM,))
def draft(req):
    cur = cur_rule(req)
    sla, docs, b = json.loads(cur["sla"]), json.loads(cur["docs"]), req.body or {}
    if "sla" not in b and "docs" not in b:
        raise ApiError(422, "VALIDATION_ERROR", "Provide 'sla' (stage time limits) and/or 'docs' (required documents per application type)")
    if not isinstance(b.get("sla", {}), dict) or not isinstance(b.get("docs", {}), dict):
        raise ApiError(422, "VALIDATION_ERROR", "'sla' and 'docs' must be objects")
    for k, v in b.get("sla", {}).items():
        if k not in sla or k == "Decision recorded":
            raise ApiError(422, "VALIDATION_ERROR", f"Unknown stage '{k}'")
        sla[k] = v
    for k, v in b.get("docs", {}).items():
        if k not in APP_TYPES:
            raise ApiError(422, "VALIDATION_ERROR", f"Unknown application type '{k}'")
        if not isinstance(v, list):
            raise ApiError(422, "VALIDATION_ERROR", f"Required documents for '{k}' must be a list")
        docs[k] = v
    old = req.db.execute("SELECT version FROM rules WHERE status IN ('draft','validated','previewed')").fetchone()
    note = req.field("note", required=False) or ""
    if old:
        req.db.execute("UPDATE rules SET status='draft',sla=?,docs=?,note=?,err=NULL,preview=NULL,by_user=?,at=? WHERE version=?",
                       (json.dumps(sla), json.dumps(docs), note, req.user["username"], req.day, old[0]))
        v = old[0]
    else:
        v = req.db.execute("SELECT MAX(version) FROM rules").fetchone()[0] + 1
        req.db.execute("INSERT INTO rules(version,status,sla,docs,by_user,at,note) VALUES(?,?,?,?,?,?,?)", (v, "draft", json.dumps(sla), json.dumps(docs), req.user["username"], req.day, note))
    req.audit("rule.drafted", f"DEMO-RULES-01 v{v}", note)
    return 201, {"version": v, "status": "draft"}


@route("POST", "/api/rules/draft/validate", roles=(ADM,))
def validate(req):
    d, cur = open_draft(req), cur_rule(req)
    sla, docs, csla, cdocs = json.loads(d["sla"]), json.loads(d["docs"]), json.loads(cur["sla"]), json.loads(cur["docs"])
    errs = []
    bad = [k for k, v in sla.items() if k != "Decision recorded" and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 60)]
    if bad:
        errs.append("Limits must be whole days 1-60: " + ", ".join(bad))
    for t, lst in docs.items():
        unknown = [x for x in lst if x not in DOCS]
        if unknown:
            errs.append(f"{t}: unknown documents {unknown}")
        if len(set(lst)) != len(lst):
            errs.append(f"{t}: duplicate documents")
        if "Application form" not in lst:
            errs.append(f"{t}: Application form is always required")
    changed = [n for n, x, y in (("Stage time limits", sla, csla), ("Required documents", norm(docs), norm(cdocs))) if x != y]
    if not errs and not changed:
        errs.append("No change from current version")
    if not errs and not d["note"]:
        errs.append("Add a change summary")
    err = "; ".join(errs) or None
    req.db.execute("UPDATE rules SET status=?,err=? WHERE version=?", ("draft" if err else "validated", err, d["version"]))
    req.audit("rule.validated" if not err else "rule.validation_failed", f"DEMO-RULES-01 v{d['version']}", err or "ok: " + ", ".join(changed))
    return {"version": d["version"], "valid": not err, "error": err, "changed_sections": changed}


@route("POST", "/api/rules/draft/preview", roles=(ADM,))
def preview(req):
    d = open_draft(req)
    if d["status"] == "draft":
        raise ApiError(409, "NOT_VALIDATED", "Validate the draft before previewing impact")
    sla, docs, ctx, rows = json.loads(d["sla"]), json.loads(d["docs"]), req.ctx, []
    for a in ctx["apps"]:
        if a["stage"] < 6:
            o, n = E.risk(a, ctx), E.risk(a, ctx, sla, docs)
            now = E.missing(a, ctx)
            new = [x for x in E.missing(a, ctx, docs) if x not in now]
            rows.append({"application_id": a["id"], "type": a["type"], "stage": STAGES[a["stage"]][0], "current_rule_version": a["rv"], "risk_now": f"{o['category']} {o['score']}",
                         "risk_under_draft": f"{n['category']} {n['score']}", "newly_required": new, "changed": o["score"] != n["score"] or bool(new)})
    pv = {"affected": sum(r["changed"] for r in rows), "in_progress": len(rows), "applications": rows,
          "note": "Shown as if in-progress applications were migrated. Choosing 'keep' leaves them on their current version; new applications use the new version.",
          "obligations_note": "This demo rule set does not change obligation due dates; existing obligations are unaffected.", "historical_decisions": "Never rewritten"}
    req.db.execute("UPDATE rules SET status='previewed',preview=? WHERE version=?", (json.dumps(pv), d["version"]))
    req.audit("rule.previewed", f"DEMO-RULES-01 v{d['version']}", f"{pv['affected']} of {pv['in_progress']} in-progress applications affected")
    return pv


@route("POST", "/api/rules/draft/publish", roles=(ADM,))
def publish(req):
    d = open_draft(req)
    if d["status"] != "previewed":
        raise ApiError(409, "NOT_PREVIEWED", "Preview impact before publishing")
    m = req.field("migration")
    if m not in ("migrate", "keep"):
        raise ApiError(422, "VALIDATION_ERROR", "migration must be 'migrate' or 'keep' (explicit decision required)")
    req.db.execute("UPDATE rules SET status='retired' WHERE status='published'")  # old version retained for audit
    req.db.execute("UPDATE rules SET status='published',migration=?,by_user=?,at=? WHERE version=?", (m, req.user["username"], req.day, d["version"]))
    if m == "migrate":
        req.db.execute("UPDATE applications SET rv=? WHERE stage<6", (d["version"],))
    req.audit("rule.published", f"DEMO-RULES-01 v{d['version']}", f"migration={m}; v{d['version'] - 1} retained")
    return {"version": d["version"], "status": "published", "migration": m}


# ---------- documents / actions / audit / demo ----------
@route("GET", "/api/document-checks")
def doc_checks(req):
    rows = []
    for a in req.scoped_apps():
        if req.query.get("application_id") in (None, a["id"]):
            rows += [{"application_id": a["id"], **f, "note": "Review flag only; a human decides"} for f in E.findings(a, req.ctx)]
    return page(req, rows)


@route("GET", "/api/actions")
def actions(req):
    return {"role": req.user["role"], "items": E.next_actions(req.user, req.db, req.ctx)}


@route("GET", "/api/audit")
def audit_log(req):
    q, args = "SELECT * FROM audit", ()
    if req.query.get("ref"):
        q, args = q + " WHERE ref=?", (req.query["ref"],)
    rows = [dict(r) for r in req.db.execute(q + " ORDER BY id DESC", args)]
    if req.user["role"] == INST:
        ids = {a["id"] for a in req.scoped_apps()}
        rows = [r for r in rows if r["ref"] in ids or r["ref"].startswith("OB-")]
    return page(req, rows)


@route("POST", "/api/demo/advance-day", roles=(ADM,))
def next_day(req):
    req.db.execute("UPDATE meta SET v=? WHERE k='day'", (str(req.day + 1),))
    req.audit("demo.day_advanced", "", f"day {req.day + 1}")
    return {"day": req.day + 1}


@route("POST", "/api/demo/reset", roles=(ADM,))
def reset(req):
    D.seed(req.db)
    return {"status": "reseeded"}


# ---------- HTTP plumbing ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "FlowIntel/0.1"

    def log_message(self, *a):
        pass

    def send(self, status, body):
        raw = json.dumps(body, default=str).encode()
        self.send_response(status)
        for k, v in (("Content-Type", "application/json"), ("Access-Control-Allow-Origin", ALLOW_ORIGIN), ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
                     ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"), ("Content-Length", str(len(raw)))):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def handle_any(self, method):
        u = urlparse(self.path)
        if method == "GET" and u.path in ("/", "/index.html"):  # serve the prototype UI from the same origin as the API
            with open(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"), "rb") as f:
                raw = f.read()
            self.send_response(200)
            for k, v in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(raw)))):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
            return
        try:
            for m, rx, roles, auth, fn in ROUTES:
                mt = rx.match(u.path)
                if m == method and mt:
                    break
            else:
                raise ApiError(404, "NOT_FOUND", "No such endpoint")
            body = None
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    raise ApiError(413, "TOO_LARGE", "Body too large")
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    assert isinstance(body, dict)
                except Exception:
                    raise ApiError(400, "BAD_JSON", "Body must be a JSON object")
            with LOCK:
                db = self.server.db
                user = None
                if auth:
                    p = unsign(self.headers.get("Authorization", "").removeprefix("Bearer ").strip())
                    row = db.execute("SELECT * FROM users WHERE id=?", (p["u"],)).fetchone() if p else None
                    if not row:
                        raise ApiError(401, "UNAUTHENTICATED", "Missing or invalid token")
                    user = dict(row)
                req = Req(db, user, body, {k: v[0] for k, v in parse_qs(u.query).items()})
                if roles:
                    req.need(*roles)
                try:
                    out = fn(req, **mt.groupdict())
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
            status, payload = out if isinstance(out, tuple) else (200, out)
            self.send(status, payload)
        except ApiError as e:
            self.send(e.status, {"error": {"code": e.code, "message": e.message, "details": e.details}})
        except Exception:
            self.send(500, {"error": {"code": "INTERNAL", "message": "Unexpected server error", "details": None}})

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_OPTIONS(self): self.send(204, {})


def make_server(host="127.0.0.1", port=8000, db_path=None):
    db = D.connect(db_path)
    db.executescript(D.SCHEMA)
    if "docs" not in [r[1] for r in db.execute("PRAGMA table_info(rules)")]:  # upgrade an older database file
        db.execute("ALTER TABLE rules ADD COLUMN docs TEXT")
        db.execute("UPDATE rules SET docs=?", (json.dumps(D.BASE_DOCS),))
        db.commit()
    if not db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        D.seed(db)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.db = db
    return srv
