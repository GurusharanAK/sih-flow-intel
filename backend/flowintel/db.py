"""SQLite schema + synthetic seed data. All records are synthetic; rules are DEMO values."""
import hmac, hashlib, json, os, secrets, sqlite3

DB_PATH = os.environ.get("FLOWINTEL_DB", "flowintel.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, role TEXT NOT NULL, iid INTEGER);
CREATE TABLE IF NOT EXISTS institutions(id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, iid INTEGER NOT NULL REFERENCES institutions(id), type TEXT NOT NULL,
  stage INTEGER NOT NULL, entered INTEGER NOT NULL, have TEXT NOT NULL, rv INTEGER NOT NULL, clar_since INTEGER, ovr TEXT);
CREATE TABLE IF NOT EXISTS handoffs(id INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL REFERENCES applications(id),
  from_role TEXT, to_role TEXT, from_stage TEXT, to_stage TEXT, sent INTEGER, ack INTEGER, ack_by TEXT, expected_by INTEGER, missing TEXT);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY AUTOINCREMENT, app_id TEXT NOT NULL REFERENCES applications(id),
  name TEXT, expiry INTEGER, code TEXT, inst_name TEXT, issuer TEXT);
CREATE TABLE IF NOT EXISTS obligations(id TEXT PRIMARY KEY, iid INTEGER NOT NULL REFERENCES institutions(id), title TEXT, source TEXT,
  rule TEXT, due INTEGER, status TEXT, note TEXT, ver_by TEXT, ver_at INTEGER);
CREATE TABLE IF NOT EXISTS obligation_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ob_id TEXT REFERENCES obligations(id), day INTEGER, actor TEXT, event TEXT);
CREATE TABLE IF NOT EXISTS rules(version INTEGER PRIMARY KEY, status TEXT NOT NULL, sla TEXT NOT NULL, by_user TEXT, at INTEGER, note TEXT,
  migration TEXT, err TEXT, preview TEXT, docs TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, day INTEGER, ts TEXT DEFAULT CURRENT_TIMESTAMP, actor TEXT, role TEXT, action TEXT, ref TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_app_stage ON applications(stage);
CREATE INDEX IF NOT EXISTS ix_ho_app ON handoffs(app_id);
CREATE INDEX IF NOT EXISTS ix_audit_ref ON audit(ref);
"""
ROLES = ["Institution User", "Regional Officer", "Scrutiny Officer", "EVC Coordinator", "SAC/SHC Reviewer", "AICTE Administrator"]
STAGES = [("Submitted", ROLES[1], []), ("Scrutiny", ROLES[2], ["Scrutiny report"]),
          ("EVC Scheduling", ROLES[3], ["EVC date fixed"]), ("EVC Visit", ROLES[3], ["EVC report"]),
          ("SAC/SHC Review", ROLES[4], ["SAC decision note"]), ("Final Processing", ROLES[1], []), ("Decision recorded", "-", [])]
# Document catalogue (institution-provided) with the stage index at which each is first needed. Which documents an
# application type needs is NOT hard-coded: it comes from the published rule version (rules.docs).
# Internal (non-institution) prerequisites and the only role that can record each one.
PRODUCER = {"Scrutiny report": ROLES[2], "EVC date fixed": ROLES[3], "EVC report": ROLES[3], "SAC decision note": ROLES[4]}
DOCS = ["Application form", "Land document", "Fee receipt", "Faculty list", "Fire safety certificate", "Affiliation letter"]
DOC_STAGE = {"Application form": 0, "Land document": 0, "Fee receipt": 1, "Faculty list": 1, "Fire safety certificate": 1, "Affiliation letter": 1}
APP_TYPES = ["New Institution", "Extension of Approval", "Increase in Intake", "Closure"]
BASE_DOCS = {t: ["Application form", "Land document", "Fee receipt", "Faculty list"] for t in APP_TYPES}  # DEMO baseline


def required_items(stage, typ, docs_rule):
    """Prerequisites to leave `stage`: rule-driven documents for the type plus internal (non-institution) items."""
    return [d for d in docs_rule.get(typ, []) if DOC_STAGE.get(d) == stage] + STAGES[stage][2]

BASE_SLA = {"Submitted": 3, "Scrutiny": 7, "EVC Scheduling": 5, "EVC Visit": 10, "SAC/SHC Review": 10, "Final Processing": 5, "Decision recorded": 0}
INSTITUTIONS = [("Sahyadri Institute of Technology", "DEMO-1001"), ("Nilgiri College of Engineering", "DEMO-1002"), ("Kaveri Polytechnic", "DEMO-1003"),
                ("Vindhya School of Management", "DEMO-1004"), ("Aravali Institute of Pharmacy", "DEMO-1005"), ("Coastal Tech Institute", "DEMO-1006")]
DEMO_USERS = [("institution", ROLES[0], 0), ("regional", ROLES[1], None), ("scrutiny", ROLES[2], None), ("evc", ROLES[3], None),
              ("sac", ROLES[4], None), ("admin", ROLES[5], None)]
DEMO_PASSWORD = "demo1234"  # prototype auth only


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def check_pw(pw, stored):
    return hmac.compare_digest(hash_pw(pw, stored.split("$")[0]), stored)


def connect(path=None):
    c = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def seed(db):
    """Reset to a coherent synthetic demo state (day 0)."""
    db.executescript(SCHEMA)
    for t in ["audit", "obligation_events", "obligations", "documents", "handoffs", "applications", "rules", "institutions", "users", "meta"]:
        db.execute(f"DELETE FROM {t}")
    db.execute("INSERT INTO meta VALUES('day','0')")
    for i, (n, c) in enumerate(INSTITUTIONS):
        db.execute("INSERT INTO institutions VALUES(?,?,?)", (i, n, c))
    for u, r, iid in DEMO_USERS:
        db.execute("INSERT INTO users(username,pw,role,iid) VALUES(?,?,?,?)", (u, hash_pw(DEMO_PASSWORD), r, iid))
    db.execute("INSERT INTO rules VALUES(1,'published',?,?,?,?,NULL,NULL,NULL,?)", (json.dumps(BASE_SLA), ROLES[5], -30, "DEMO baseline v1 (not official policy)", json.dumps(BASE_DOCS)))
    A, L, F, Q, SR, ED, ER, SN = DOCS[0], DOCS[1], DOCS[2], DOCS[3], "Scrutiny report", "EVC date fixed", "EVC report", "SAC decision note"
    rows = [(1, 0, "New Institution", 1, -9, [A, L, F, Q], -9, -8, None), (2, 1, "Extension of Approval", 1, -4, [A, L, F], -4, None, None),
            (3, 2, "Increase in Intake", 2, -6, [A, L, F, Q, SR], -6, -5, None), (4, 3, "New Institution", 2, -8, [A, L, F, Q, SR], -3, None, None),
            (5, 0, "Extension of Approval", 0, -1, [A], -1, None, None), (6, 4, "New Institution", 3, -3, [A, L, F, Q, SR, ED], -3, -2, None),
            (7, 5, "Closure", 4, -12, [A, L, F, Q, SR, ED, ER], -12, -11, None), (8, 1, "Increase in Intake", 1, -6, [A, L, F, Q], -6, -5, -7),
            (9, 2, "Extension of Approval", 5, 0, [A, L, F, Q, SR, ED, ER, SN], 0, None, None), (10, 3, "Increase in Intake", 0, -5, [A], -5, None, None),
            (11, 4, "New Institution", 1, -1, [A, L], -1, -1, None), (12, 0, "New Institution", 3, -9, [A, L, F, Q, SR, ED], -9, -8, None)]
    for n, iid, typ, st, ent, have, sent, ack, clar in rows:
        aid = f"APP-26{n:02d}"
        db.execute("INSERT INTO applications VALUES(?,?,?,?,?,?,1,?,NULL)", (aid, iid, typ, st, ent, json.dumps(have), clar))
        frm = STAGES[st - 1][1] if st else ROLES[0]
        db.execute("INSERT INTO handoffs(app_id,from_role,to_role,from_stage,to_stage,sent,ack,ack_by,expected_by,missing) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (aid, frm, STAGES[st][1], STAGES[st - 1][0] if st else "Applicant", STAGES[st][0], sent, ack, STAGES[st][1] if ack is not None else None,
                    sent + BASE_SLA[STAGES[st][0]], json.dumps([x for x in required_items(st, typ, BASE_DOCS) if x not in have])))
        for d in [x for x in have if x in DOCS]:
            db.execute("INSERT INTO documents(app_id,name,expiry,code,inst_name,issuer) VALUES(?,?,?,?,?,?)", (aid, d, None, INSTITUTIONS[iid][1], INSTITUTIONS[iid][0], "Synthetic Authority"))
    db.execute("UPDATE documents SET expiry=-10 WHERE app_id='APP-2601' AND name=?", (L,))
    db.execute("INSERT INTO documents(app_id,name,expiry,code,inst_name,issuer) SELECT app_id,name,expiry,code,inst_name,issuer FROM documents WHERE app_id='APP-2603' AND name=?", (Q,))
    db.execute("UPDATE documents SET inst_name='Vindhya School of Mgmt' WHERE app_id='APP-2604' AND name=?", (A,))
    db.execute("UPDATE documents SET code='DEMO-0000' WHERE app_id='APP-2602' AND name=?", (F,))
    db.execute("UPDATE documents SET expiry=20 WHERE app_id='APP-2606' AND name=?", (L,))
    db.execute("UPDATE documents SET issuer='' WHERE app_id='APP-2611' AND name=?", (L,))
    obs = [("OB-1", 0, "Fire safety certificate", "Undertaking, APP-2601", -5, "pending", ""), ("OB-2", 0, "Updated faculty list", "Approval condition, APP-2612", 10, "pending", ""),
           ("OB-3", 1, "Lease document renewal", "Approval condition, APP-2602", 20, "submitted", ""), ("OB-4", 2, "Annual self-disclosure", "Undertaking, APP-2603", -2, "correction", "Evidence is unsigned"),
           ("OB-5", 3, "Affidavit on land use", "Undertaking, APP-2604", 40, "pending", ""), ("OB-6", 4, "Audited accounts", "Approval condition, APP-2606", 6, "submitted", ""),
           ("OB-7", 5, "Lease document: expiring", "Approval condition, APP-2607", 25, "verified", ""), ("OB-8", 1, "Lab equipment list", "Approval condition, APP-2608", -12, "verified", "")]
    for o in obs:
        db.execute("INSERT INTO obligations VALUES(?,?,?,?,'DEMO-CMP-01',?,?,?,NULL,NULL)", o)
    db.execute("UPDATE obligations SET ver_by=?,ver_at=-3 WHERE id='OB-7'", (ROLES[1],))
    db.execute("UPDATE obligations SET ver_by=?,ver_at=-20 WHERE id='OB-8'", (ROLES[1],))
    db.execute("INSERT INTO audit(day,actor,role,action,ref,detail) VALUES(0,'system','System','seed','','Synthetic demo data loaded')")
    db.commit()
