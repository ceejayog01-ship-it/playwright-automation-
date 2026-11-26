"""Tiny in-memory PostgREST stand-in: enough of upsert/select to exercise the sink over real HTTP."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

KEY = "test-service-key"


class FakePG:
    def __init__(self):
        self.tables, self.calls, self.fail_next = {}, [], 0
        pg = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _auth(self):
                return self.headers.get("apikey") == KEY and self.headers.get("Authorization") == f"Bearer {KEY}"

            def _send(self, code, body=b""):
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                u = urlparse(self.path); q = parse_qs(u.query); table = u.path.rsplit("/", 1)[-1]
                rows = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                pg.calls.append(("POST", table, len(rows), self.headers.get("Prefer")))
                if pg.fail_next:
                    pg.fail_next -= 1; return self._send(503)
                if not self._auth():
                    return self._send(401, b'{"message":"bad key"}')
                keys = q["on_conflict"][0].split(",")
                ignore = "ignore-duplicates" in (self.headers.get("Prefer") or "")
                t = pg.tables.setdefault(table, {})
                for r in rows:
                    k = tuple(str(r[c]) for c in keys)
                    if k in t and ignore:
                        continue
                    t[k] = {**t.get(k, {}), **r}
                self._send(201)

            def do_GET(self):
                u = urlparse(self.path); q = parse_qs(u.query); table = u.path.rsplit("/", 1)[-1]
                pg.calls.append(("GET", table, 0, None))
                if not self._auth():
                    return self._send(401)
                rows = list(pg.tables.get(table, {}).values())
                for col, vals in q.items():
                    if col in ("select", "order", "limit", "offset"):
                        continue
                    op, _, v = vals[0].partition(".")
                    cast = (lambda x: x) if not isinstance(rows and rows[0].get(col), (int, float)) else float
                    rows = [r for r in rows if (cast(str(r[col])) == cast(v) if op == "eq" else cast(str(r[col])) >= cast(v) if op == "gte" else cast(str(r[col])) > cast(v))]
                if "order" in q:
                    col, _, d = q["order"][0].partition(".")
                    rows.sort(key=lambda r: r[col], reverse=(d == "desc"))
                off, lim = int(q.get("offset", ["0"])[0]), int(q.get("limit", ["1000"])[0])
                self._send(200, json.dumps(rows[off:off + lim]).encode())

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
