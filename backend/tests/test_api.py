import json, os, tempfile, threading, unittest, urllib.request, urllib.error
from flowintel import server

class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.srv = server.make_server("127.0.0.1", 0, os.path.join(cls.tmp, "t.db"))
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {}
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
    def setUp(self):
        self.call("POST", "/api/demo/reset", "admin")  # fresh data for every test
    def call(self, method, path, user=None, body=None):
        if user and user not in self.tok:
            r = urllib.request.Request(self.base + "/api/auth/login", json.dumps({"username": user, "password": "demo1234"}).encode(), {"Content-Type": "application/json"})
            self.tok[user] = json.load(urllib.request.urlopen(r))["token"]
        h = {"Content-Type": "application/json"}
        if user: h["Authorization"] = "Bearer " + self.tok[user]
        r = urllib.request.Request(self.base + path, json.dumps(body or {}).encode() if method == "POST" else None, h, method=method)
        try:
            with urllib.request.urlopen(r) as x: return x.status, json.load(x)
        except urllib.error.HTTPError as e: return e.code, json.load(e)

    def test_health_and_login(self):
        self.assertEqual(self.call("GET", "/api/health")[1]["db"], True)
        self.assertEqual(self.call("GET", "/api/me")[0], 401)
        r = urllib.request.Request(self.base + "/api/auth/login", json.dumps({"username": "admin", "password": "wrong"}).encode(), {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as c: urllib.request.urlopen(r)
        self.assertEqual(c.exception.code, 401)

    def test_s1_create_and_queue(self):  # Scenario 1
        s, b = self.call("POST", "/api/applications", "institution", {"type": "New Institution"})
        self.assertEqual(s, 201); aid = b["id"]
        s, q = self.call("GET", "/api/actions", "regional")
        self.assertTrue(any(aid in a["action"] for a in q["items"]))
        self.assertEqual(self.call("POST", "/api/applications", "institution", {"type": "Bogus"})[0], 422)

    def test_s2_blocked_transition(self):  # Scenario 2
        s, b = self.call("POST", "/api/applications/APP-2602/advance", "scrutiny")
        self.assertEqual(s, 409); self.assertEqual(b["error"]["code"], "TRANSITION_BLOCKED")
        self.assertIn("Missing prerequisite: Faculty list", b["error"]["details"])
        self.assertEqual(self.call("GET", "/api/applications/APP-2602", "scrutiny")[1]["stage"], "Scrutiny")

    def test_s3_handoff_ack_and_audit(self):  # Scenario 3
        aid = self.call("POST", "/api/applications", "institution", {"type": "Closure"})[1]["id"]
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/handoff/ack", "regional")[0], 200)
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/advance", "regional")[0], 409)  # land doc missing
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/documents", "institution", {"item": "Land document"})[0], 201)
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/advance", "regional")[0], 200)
        d = self.call("GET", f"/api/applications/{aid}", "scrutiny")[1]
        self.assertFalse(d["current_handoff"]["acknowledged"]); self.assertEqual(d["current_handoff"]["to_role"], "Scrutiny Officer")
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/handoff/ack", "regional")[0], 403)  # wrong recipient
        self.assertEqual(self.call("POST", f"/api/applications/{aid}/handoff/ack", "scrutiny")[0], 200)
        acts = [a["action"] for a in self.call("GET", f"/api/audit?ref={aid}", "admin")[1]["items"]]
        self.assertIn("handoff.acknowledged", acts); self.assertIn("stage.advanced", acts)

    def test_s4_risk_evidence(self):  # Scenario 4
        r = self.call("GET", "/api/applications/APP-2604", "regional")[1]["risk"]
        self.assertEqual(r["category"], "High")
        self.assertGreaterEqual(r["score"], 55); self.assertTrue(all(f["evidence"] for f in r["factors"]))
        self.assertIn("AICTE-side", r["bottleneck"]); self.assertIn("prototype", r["disclaimer"].lower())
        self.call("POST", "/api/demo/advance-day", "admin")
        self.assertGreater(self.call("GET", "/api/applications/APP-2604", "regional")[1]["risk"]["score"] + 1, r["score"])
        s, o = self.call("POST", "/api/applications/APP-2604/risk-override", "regional", {"category": "Low", "reason": "Visit confirmed by phone"})
        self.assertEqual(s, 200); self.assertEqual(self.call("POST", "/api/applications/APP-2604/risk-override", "regional", {"category": "Low"})[0], 422)
        self.assertEqual(self.call("POST", "/api/applications/APP-2604/risk-override", "scrutiny", {"category": "Low", "reason": "x"})[0], 403)

    def test_s5_compliance(self):  # Scenario 5
        self.assertEqual(self.call("POST", "/api/obligations/OB-1/evidence", "institution", {"evidence": "fire_noc.pdf"})[0], 200)
        self.assertEqual(self.call("POST", "/api/obligations/OB-1/verify", "institution")[0], 403)
        self.assertEqual(self.call("POST", "/api/obligations/OB-1/verify", "regional")[0], 200)
        self.assertEqual(self.call("POST", "/api/obligations/OB-6/correction", "regional", {})[0], 422)
        self.assertEqual(self.call("POST", "/api/obligations/OB-6/correction", "regional", {"reason": "Unsigned"})[1]["status"], "correction")
        o = {x["id"]: x for x in self.call("GET", "/api/obligations", "regional")[1]["items"]}
        self.assertEqual(o["OB-1"]["ver_by"], "Regional Officer"); self.assertTrue(o["OB-1"]["history"])
        self.assertEqual(self.call("POST", "/api/obligations/OB-5/evidence", "institution", {})[0], 404)  # other institution's

    def test_s6_rule_lifecycle(self):  # Scenario 6
        self.assertEqual(self.call("POST", "/api/rules/draft/preview", "admin")[0], 409)
        self.call("POST", "/api/rules/draft", "admin", {"sla": {"Scrutiny": 70}, "note": "bad"})
        self.assertFalse(self.call("POST", "/api/rules/draft/validate", "admin")[1]["valid"])
        self.call("POST", "/api/rules/draft", "admin", {"sla": {"Scrutiny": 5}, "note": "Shorter scrutiny (demo)"})
        self.assertTrue(self.call("POST", "/api/rules/draft/validate", "admin")[1]["valid"])
        self.assertEqual(self.call("POST", "/api/rules/draft/publish", "admin", {"migration": "keep"})[0], 409)  # not previewed
        pv = self.call("POST", "/api/rules/draft/preview", "admin")[1]
        self.assertGreater(pv["affected"], 0)
        self.assertEqual(self.call("POST", "/api/rules/draft/publish", "admin", {})[0], 422)  # explicit decision required
        self.assertEqual(self.call("POST", "/api/rules/draft/publish", "admin", {"migration": "keep"})[0], 200)
        v = {x["version"]: x for x in self.call("GET", "/api/rules", "regional")[1]["versions"]}
        self.assertEqual((v[1]["status"], v[2]["status"]), ("retired", "published")); self.assertEqual(v[1]["sla"]["Scrutiny"], 7)
        self.assertEqual(self.call("GET", "/api/applications/APP-2601", "regional")[1]["rule_version"], 1)  # not silently migrated

    def test_s7_document_flags(self):  # Scenario 7
        f = self.call("GET", "/api/document-checks?application_id=APP-2601", "scrutiny")[1]["items"]
        self.assertTrue(any(x["finding"] == "Expired document" for x in f))
        kinds = {x["finding"] for x in self.call("GET", "/api/document-checks?size=100", "scrutiny")[1]["items"]}
        for k in ("Duplicate document record", "Institution ID mismatch", "Name mismatch", "Missing metadata", "Missing required document"): self.assertIn(k, kinds)
        self.assertEqual(self.call("GET", "/api/applications/APP-2601", "scrutiny")[1]["stage"], "Scrutiny")  # not auto-rejected

    def test_s8_unauthorised(self):  # Scenario 8
        self.assertEqual(self.call("POST", "/api/rules/draft", "institution", {"sla": {"Scrutiny": 3}})[0], 403)
        self.assertEqual(self.call("POST", "/api/rules/draft", "regional", {"sla": {"Scrutiny": 3}})[0], 403)
        self.assertEqual(self.call("POST", "/api/applications/APP-2601/advance", "institution")[0], 403)
        self.assertEqual(self.call("POST", "/api/demo/reset", "institution")[0], 403)
        self.assertEqual(self.call("GET", "/api/applications/APP-2602", "institution")[0], 404)  # other institution
        self.assertEqual(self.call("POST", "/api/applications", "regional", {"type": "Closure"})[0], 403)
        self.assertTrue(any(a["action"] == "DENIED" for a in self.call("GET", "/api/audit?size=100", "admin")[1]["items"]))

    def test_s9_consistency(self):  # Scenario 9
        d = self.call("GET", "/api/dashboard", "admin")[1]
        apps = self.call("GET", "/api/applications?size=100", "admin")[1]
        self.assertEqual(d["total_applications"], apps["total"]); self.assertEqual(sum(d["by_stage"].values()), apps["total"])
        self.assertEqual(d["high_risk_applications"], len(self.call("GET", "/api/applications?risk=High&size=100", "admin")[1]["items"]) - sum(1 for a in apps["items"] if a["risk_category"] == "High" and a["stage"] == "Decision recorded"))
        self.assertEqual(d["unacknowledged_handoffs"], self.call("GET", "/api/handoffs?status=unacknowledged", "admin")[1]["total"])
        self.assertEqual(d["obligations"]["total"], self.call("GET", "/api/obligations", "admin")[1]["total"])
        ids = {a["link"]["id"] for a in self.call("GET", "/api/actions", "admin")[1]["items"] if a["link"]["type"] == "application"}
        self.assertTrue(ids <= {a["id"] for a in apps["items"]})

    def test_decision_by_human_only(self):
        s, b = self.call("POST", "/api/applications/APP-2609/handoff/ack", "regional"); self.assertEqual(s, 200)
        self.assertEqual(self.call("POST", "/api/applications/APP-2609/advance", "admin")[0], 403)
        self.assertEqual(self.call("POST", "/api/applications/APP-2609/advance", "regional")[0], 200)
        self.assertEqual(self.call("GET", "/api/applications/APP-2609", "regional")[1]["stage"], "Decision recorded")

if __name__ == "__main__":
    unittest.main()
