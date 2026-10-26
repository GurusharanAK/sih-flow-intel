"""Deterministic business logic: risk scoring, document checks, obligation status, next actions.
Scores are PROTOTYPE ESTIMATES from transparent rules, not validated predictions. No statutory decisions are made here."""
import json
from .db import DOCS, ROLES, STAGES

DISCLAIMER = "Prototype estimate from transparent rules; not a validated prediction. Based only on recorded events; not an assignment of blame."


def load_ctx(db):
    day = int(db.execute("SELECT v FROM meta WHERE k='day'").fetchone()[0])
    slas = {r["version"]: json.loads(r["sla"]) for r in db.execute("SELECT version,sla FROM rules")}
    inst = {r["id"]: dict(r) for r in db.execute("SELECT * FROM institutions")}
    apps, loads = [], {}
    for r in db.execute("SELECT * FROM applications ORDER BY id"):
        a = dict(r)
        a["have"] = json.loads(a["have"])
        a["ovr"] = json.loads(a["ovr"]) if a["ovr"] else None
        h = db.execute("SELECT * FROM handoffs WHERE app_id=? ORDER BY id DESC LIMIT 1", (a["id"],)).fetchone()
        a["ho"] = dict(h) if h else None
        a["docs"] = [dict(d) for d in db.execute("SELECT * FROM documents WHERE app_id=?", (a["id"],))]
        apps.append(a)
        if a["stage"] < 6:
            loads[STAGES[a["stage"]][1]] = loads.get(STAGES[a["stage"]][1], 0) + 1
    return {"day": day, "slas": slas, "inst": inst, "apps": apps, "loads": loads}


def missing(a):
    return [x for x in STAGES[a["stage"]][2] if x not in a["have"]]


def owner(a):
    return STAGES[a["stage"]][1]


def risk(a, ctx, ov=None):
    if a["stage"] >= 6:
        return {"score": 0, "category": "Low", "factors": [], "bottleneck": "Closed", "next_action": "None", "responsible_role": "-", "disclaimer": DISCLAIMER}
    name, role, _ = STAGES[a["stage"]]
    day, f = ctx["day"], []
    el, sla = day - a["entered"], (ov or ctx["slas"][a["rv"]])[name]
    s = 0

    def add(p, t, ev):
        nonlocal s
        s += p
        f.append({"points": p, "factor": t, "evidence": ev})
    if el >= sla:
        add(40, "Past configured stage time", f"{el}d in {name}; configured {sla}d")
    elif el / sla >= .6:
        add(20, "Approaching stage deadline", f"{el}d of {sla}d used")
    ms = missing(a)
    if ms:
        add(min(30, 12 * len(ms)), "Missing prerequisites", ", ".join(ms))
    h = a["ho"]
    un = bool(h) and h["ack"] is None
    w = day - h["sent"] if un else 0
    if un:
        add(20, "Handoff unacknowledged", f"Sent {w}d ago to {role}, no acknowledgement") if w >= 2 else add(5, "Handoff awaiting acknowledgement", f"Sent {w}d ago")
    if a["clar_since"] is not None:
        c = day - a["clar_since"]
        add(20 if c > 5 else 10, "Clarification pending", f"Requested {c}d ago; no institution response")
    if ctx["loads"].get(role, 0) >= 3:
        add(5, "High queue load", f"{ctx['loads'][role]} open cases with {role} (synthetic)")
    s = min(100, s)
    inst = [x for x in ms if x in DOCS]
    if un and w >= 2:
        bn, na, nr = "AICTE-side (handoff unacknowledged)", f"Acknowledge handoff for {a['id']}", role
    elif a["clar_since"] is not None or inst:
        bn, na, nr = "Institution-side", ("Respond to clarification" if a["clar_since"] is not None else "Upload " + ", ".join(inst)), ROLES[0]
    elif a["stage"] in (2, 3):
        bn, na, nr = "Scheduling-side", ("Resolve: " + ", ".join(ms) if ms else "Confirm visit schedule"), role
    elif s >= 30 and el >= sla:
        bn, na, nr = "AICTE-side (stage time exceeded)", "Process or escalate the stage", role
    else:
        bn, na, nr = "Undetermined", ("Review: " + ", ".join(ms) if ms else "Monitor"), role
    return {"score": s, "category": "High" if s >= 55 else "Medium" if s >= 30 else "Low", "factors": f, "bottleneck": bn,
            "next_action": na, "responsible_role": nr, "disclaimer": DISCLAIMER}


def category(a, ctx):
    return a["ovr"]["category"] if a["ovr"] else risk(a, ctx)["category"]


def findings(a, ctx):
    """Review flags only. A human decides whether a document is acceptable."""
    day, inst, out, need = ctx["day"], ctx["inst"][a["iid"]], [], []
    for s in STAGES[:a["stage"] + 1]:
        need += [x for x in s[2] if x in DOCS and x not in need]
    F = lambda sev, t, ev, fld, fix: out.append({"severity": sev, "finding": t, "evidence": ev, "fields": fld, "recommendation": fix})
    for n in need:
        if not any(d["name"] == n for d in a["docs"]):
            F("High", "Missing required document", f"{n} not on file at {STAGES[a['stage']][0]}", ["document.name"], f"Request {n} from the institution")
    seen = set()
    for d in a["docs"]:
        if d["name"] in seen:
            F("Medium", "Duplicate document record", f"{d['name']} recorded twice", ["document.name"], "Keep one record after review")
        seen.add(d["name"])
        e = d["expiry"]
        if e is not None and e < day:
            F("High", "Expired document", f"{d['name']} expired {day - e}d ago", ["expiry_date"], f"Ask for renewed {d['name']}")
        elif e is not None and e - day <= 30:
            F("Low", "Document expiring soon", f"{d['name']} expires in {e - day}d", ["expiry_date"], "Plan renewal")
        if d["code"] != inst["code"]:
            F("High", "Institution ID mismatch", f"{d['name']} shows {d['code']}; application is {inst['code']}", ["institution_id"], "Confirm the correct ID")
        if d["inst_name"] != inst["name"]:
            F("Medium", "Name mismatch", f"'{d['inst_name']}' vs '{inst['name']}'", ["institution_name"], "Verify legal name")
        if not d["issuer"]:
            F("Medium", "Missing metadata", f"{d['name']} has no issuer", ["issuer"], "Add issuer details")
    return out


def ob_status(o, day):
    if o["status"] == "verified":
        return "Verified"
    if o["status"] == "submitted":
        return "Awaiting verification"
    if o["due"] < day:
        return "Correction needed (overdue)" if o["status"] == "correction" else "Overdue"
    if o["status"] == "correction":
        return "Correction needed"
    return "Due soon" if o["due"] - day <= 14 else "Open"


def blockers(a):
    if a["stage"] >= 6:
        return ["Decision already recorded"]
    b = []
    if a["ho"] and a["ho"]["ack"] is None:
        b.append(f"Handoff not acknowledged by {owner(a)}")
    b += [f"Missing prerequisite: {x}" for x in missing(a)]
    if a["clar_since"] is not None:
        b.append("Clarification pending from institution")
    return b


def next_actions(user, db, ctx):
    role, day, out = user["role"], ctx["day"], []
    apps = [a for a in ctx["apps"] if role != ROLES[0] or a["iid"] == user["iid"]]
    obs = [dict(o) for o in db.execute("SELECT * FROM obligations") if role != ROLES[0] or o["iid"] == user["iid"]]
    add = lambda p, t, why, kind, ref: out.append({"priority": round(p, 1), "action": t, "why": why, "link": {"type": kind, "id": ref}})
    for a in apps:
        if a["stage"] >= 6:
            continue
        r, ms, own = risk(a, ctx), missing(a), role == owner(a)
        if own and a["ho"] and a["ho"]["ack"] is None:
            add(90 + r["score"] / 10, f"Acknowledge handoff {a['id']}", f"Sent {day - a['ho']['sent']}d ago to {owner(a)}; unacknowledged work cannot be processed", "application", a["id"])
        elif own and not ms and a["clar_since"] is None:
            add(50 + r["score"] / 5, f"Advance {a['id']} from {STAGES[a['stage']][0]}", f"Prerequisites complete; {day - a['entered']}d in stage", "application", a["id"])
        if own and ms and not all(x in DOCS for x in ms):
            add(70, f"Review missing prerequisite for {a['id']}", ", ".join(ms) + " still outstanding", "application", a["id"])
        if role == ROLES[0] and any(x in DOCS for x in ms):
            add(80, f"Upload {', '.join(x for x in ms if x in DOCS)} for {a['id']}", "Missing and blocks the next stage", "application", a["id"])
        if role == ROLES[0] and a["clar_since"] is not None:
            add(85, f"Respond to clarification on {a['id']}", f"Pending {day - a['clar_since']}d", "application", a["id"])
        if role in (ROLES[1], ROLES[5]) and category(a, ctx) == "High":
            add(75, f"Review high-risk case {a['id']}", f"Score {r['score']}; bottleneck {r['bottleneck']}", "application", a["id"])
        hi = [f["finding"] for f in findings(a, ctx) if f["severity"] == "High"]
        if role in (ROLES[1], ROLES[2]) and hi:
            add(65, f"Review document flags on {a['id']}", "; ".join(hi), "document_check", a["id"])
    for o in obs:
        st = ob_status(o, day)
        if role == ROLES[0] and o["status"] in ("pending", "correction") and o["due"] - day <= 14:
            add(88 if o["due"] < day else 70, f"{'Correct' if o['status'] == 'correction' else 'Submit'} evidence: {o['title']}", f"{st}; due day {o['due']}", "obligation", o["id"])
        if role in (ROLES[1], ROLES[5]) and o["status"] == "submitted":
            add(60, f"Verify evidence: {o['title']}", "Institution submitted evidence; awaiting verification", "obligation", o["id"])
    d = db.execute("SELECT version,status FROM rules WHERE status IN ('draft','validated','previewed')").fetchone()
    if role == ROLES[5] and d:
        add(55, "Review rule change draft", f"Draft v{d['version']} is {d['status']}", "rule", str(d["version"]))
    return sorted(out, key=lambda x: -x["priority"])
