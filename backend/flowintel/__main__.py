import sys
from . import db as D, server

cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
if cmd in ("seed", "reseed"):
    c = D.connect(); D.seed(c); print("Seeded synthetic demo data (demo password: %s)" % D.DEMO_PASSWORD)
else:
    import os
    s = server.make_server(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", 8000)))
    print("Flow Intel API on http://%s:%s  (prototype: synthetic data, demo auth)" % s.server_address)
    s.serve_forever()
