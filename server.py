"""Local web server for the stock screener. Run: python server.py"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fetcher

HOST = "127.0.0.1"
PORT = 8765
STATIC = Path(__file__).parent / "static"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        elif url.path == "/api/indexes":
            out = []
            for key, meta in fetcher.INDEXES.items():
                cache = fetcher.load_cache(key)
                out.append({
                    "key": key,
                    "label": meta["label"],
                    "loaded": bool(cache and cache.get("rows")),
                    "count": len(cache["rows"]) if cache else 0,
                    "updated": cache.get("updated") if cache else None,
                    "failed": len(cache.get("failed", [])) if cache else 0,
                })
            self._json(out)

        elif url.path == "/api/stocks":
            index = query.get("index", ["sp500"])[0]
            keys = list(fetcher.INDEXES) if index == "all" else [index]
            if any(k not in fetcher.INDEXES for k in keys):
                return self._json({"error": "unknown index"}, 400)
            merged, updated, failed, outdated = {}, None, [], []
            for key in keys:
                cache = fetcher.load_cache(key)
                if not cache:
                    continue
                if cache.get("schema") != fetcher.SCHEMA:
                    outdated.append(key)
                for row in cache.get("rows", []):
                    merged.setdefault(row["ticker"], row)
                failed += cache.get("failed", [])
                if cache.get("updated") and (updated is None or cache["updated"] < updated):
                    updated = cache["updated"]  # oldest of the merged sets
            self._json({"rows": list(merged.values()), "updated": updated,
                        "failed": sorted(set(failed)), "outdated": outdated})

        elif url.path == "/api/status":
            self._json(fetcher.status)

        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/refresh":
            index = query.get("index", ["sp500"])[0]
            if index not in fetcher.INDEXES:
                return self._json({"error": "unknown index"}, 400)
            only_missing = query.get("missing", ["0"])[0] == "1"
            started = fetcher.start_refresh(index, only_missing)
            self._json({"started": started, "status": fetcher.status},
                       200 if started else 409)
        else:
            self.send_error(404)


def main():
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"Port {PORT} is already in use - the screener may already be running.")
        sys.exit(1)
    print(f"Stock screener running at http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
