"""The local server that turns Opus into an app you open in a browser.

    python3 opus.py --app

Binds to the loopback interface only, mints a session token, and opens the UI.
Nothing is exposed to the network and nothing is uploaded anywhere: this is a
desktop application that happens to draw its interface with a browser.

WHY A LOCAL SERVER IS NOT AUTOMATICALLY SAFE

A server on localhost is reachable by every other program on the machine, and
-- more subtly -- by any web page the user happens to have open, which can make
requests to 127.0.0.1 from their browser. Since this API reads folders and
writes stamped PDFs, three defences are in place rather than one:

  1. Loopback bind. Nothing off this machine can reach it at all.
  2. A session token, minted per run, required on every API call. A page that
     does not have it cannot do anything, even from the same browser.
  3. Host and Origin checks, which close DNS rebinding -- the trick where a
     hostile domain re-resolves to 127.0.0.1 so its page can talk to a local
     server as a same-origin peer.

The token lives in the URL that gets opened and in a cookie thereafter, which
is the same shape Jupyter uses for the same reason.
"""

import json
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, state

def _static_dir():
    """Where the interface lives, frozen or from source."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "webui" / "static"
    return Path(__file__).resolve().parent / "static"


STATIC = _static_dir()
DEFAULT_PORT = 7777
COOKIE = "opus_token"
MAX_BODY = 2 * 1024 * 1024        # generous for settings, far below a file upload

# Hosts a request may claim to be for. Anything else is someone else's name
# pointing at our loopback address.
def _allowed_hosts(port):
    return {"127.0.0.1:{}".format(port), "localhost:{}".format(port),
            "[::1]:{}".format(port)}


class Session:
    """One run of the app: its token and the config it is editing."""

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.config = state.load()
        self.lock = threading.Lock()


def build_handler(session, port):
    allowed = _allowed_hosts(port)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Opus"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # -- plumbing ------------------------------------------------------

        def log_message(self, fmt, *args):
            pass                        # the UI is the log

        def _headers(self, ctype, length, extra=None):
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # No remote anything: this page never needs the network.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'")
            for key, value in (extra or {}):
                self.send_header(key, value)

        def _send(self, code, body, ctype="application/json", extra=None):
            raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
            self.send_response(code)
            self._headers(ctype, len(raw), extra)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def _json(self, code, payload, extra=None):
            self._send(code, json.dumps(payload), "application/json", extra)

        # -- guards --------------------------------------------------------

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            if host not in allowed:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.netloc.lower() not in allowed:
                    return False
            return True

        def _token_from_request(self):
            query = parse_qs(urlparse(self.path).query)
            if query.get("token"):
                return query["token"][0]
            header = self.headers.get("X-Opus-Token")
            if header:
                return header
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return ""

        def _authed(self):
            return secrets.compare_digest(self._token_from_request() or "",
                                          session.token)

        # -- verbs ---------------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Forbidden", "text/plain")

            path = urlparse(self.path).path
            if not self._authed():
                return self._unauthorised(path)

            if path.startswith("/api/"):
                status, payload = self._dispatch("GET", path, None)
                return self._json(status, payload)

            return self._static(path)

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "Forbidden", "text/plain")
            path = urlparse(self.path).path
            if not self._authed():
                return self._json(401, {"error": "Not signed in to this session."})
            if not path.startswith("/api/"):
                return self._json(404, {"error": "No such endpoint"})

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                return self._json(413, {"error": "That request is too large."})
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(400, {"error": "That was not valid JSON."})

            status, payload = self._dispatch("POST", path, body)
            return self._json(status, payload)

        def _dispatch(self, method, path, body):
            # One writer at a time: the config is a single file and a run
            # appends to a hash chain, neither of which tolerates interleaving.
            with session.lock:
                return api.dispatch(method, path, body, session.config)

        def _unauthorised(self, path):
            if path in ("/", "/index.html"):
                return self._send(
                    401,
                    "<!doctype html><meta charset=utf-8><title>Opus</title>"
                    "<style>body{font:15px/1.6 system-ui;margin:14vh auto;"
                    "max-width:32rem;padding:0 1.4rem;color:#14162a;"
                    "background:#f0f0ed}h1{font:600 21px Palatino,serif}"
                    "code{background:#e4e4de;padding:2px 6px;border-radius:3px}"
                    "</style><h1>Open Opus from its own link</h1>"
                    "<p>This window is missing the session key, so it cannot "
                    "reach your files.</p><p>Go back to the terminal where you "
                    "started Opus and open the address it printed, or quit and "
                    "run <code>opus --app</code> again.</p>",
                    "text/html; charset=utf-8")
            return self._send(401, "Unauthorised", "text/plain")

        # -- static --------------------------------------------------------

        def _static(self, path):
            rel = "app.html" if path in ("/", "/index.html") else path.lstrip("/")
            target = (STATIC / rel).resolve()
            try:
                target.relative_to(STATIC.resolve())
            except ValueError:
                return self._send(404, "Not found", "text/plain")
            if not target.is_file():
                return self._send(404, "Not found", "text/plain")

            ctype, _ = mimetypes.guess_type(target.name)
            data = target.read_bytes()

            extra = []
            if rel == "app.html":
                # Move the token out of the URL and into a cookie, so it stops
                # sitting in the address bar and in browser history.
                extra.append(("Set-Cookie",
                              "{}={}; Path=/; SameSite=Strict; HttpOnly"
                              .format(COOKIE, session.token)))
            self._send(200, data, ctype or "application/octet-stream", extra)

    return Handler


def serve(port=DEFAULT_PORT, open_browser=True, log=print):
    """Run the app until interrupted."""
    if not STATIC.is_dir():
        sys.exit("The interface is missing from this install: {}".format(STATIC))

    session = Session()
    for attempt in range(12):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port + attempt),
                                        build_handler(session, port + attempt))
            port = port + attempt
            break
        except OSError:
            continue
    else:
        sys.exit("Could not find a free port near {}.".format(DEFAULT_PORT))

    url = "http://127.0.0.1:{}/?token={}".format(port, session.token)
    if log:
        log("Opus is running.")
        log("")
        log("  {}".format(url))
        log("")
        log("Settings are kept in {}".format(state.config_path()))
        log("Nothing leaves this machine. Ctrl-C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if log:
            log("\nStopped.")
    finally:
        httpd.server_close()
    return 0
