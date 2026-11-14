"""Local server for the Daily Brief page.

Serves this folder at http://localhost:8765 and lets the page's Refresh
button start the scraper. Only reachable from this computer.

    .venv\\Scripts\\python.exe server.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCK = DATA / "scrape.lock"
PORT = 8765

_proc: subprocess.Popen | None = None
_guard = threading.Lock()


def running() -> bool:
    if _proc is not None and _proc.poll() is None:
        return True
    # the hourly task may be running it too
    return LOCK.exists() and time.time() - LOCK.stat().st_mtime < 20 * 60


def generated() -> int:
    try:
        head = (DATA / "news.js").read_text(encoding="utf-8")[:80]
        m = re.search(r'"generated":(\d+)', head)
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def start_scrape() -> bool:
    global _proc
    with _guard:
        if running():
            return False
        _proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scraper.py")], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True


class Handler(SimpleHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/api/status":
            self._json({"running": running(), "generated": generated()})
        else:
            super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/refresh":
            self._json({"error": "not found"}, 404)
        elif self.headers.get("X-Daily-Brief") != "1":   # only this page may ask
            self._json({"error": "forbidden"}, 403)
        else:
            started = start_scrape()
            self._json({"started": started, "running": True})

    def end_headers(self) -> None:
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet
        pass


def main() -> int:
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=str(ROOT)))
    except OSError:
        print(f"port {PORT} is already in use - the server is probably running already")
        return 0
    print(f"Daily Brief is at http://localhost:{PORT}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
