"""Delivery by expiring link, and a small server that honours it.

WHY A LINK RATHER THAN AN ATTACHMENT

Three reasons, and they compound. A twenty-part band piece does not fit in a
mail attachment. A new sender pushing PDF attachments is spam-filter bait, and
a licence that lands in junk becomes a support call. And a link produces
download telemetry, which is itself a licensing signal -- one licensee pulling
a part eleven times from four countries is a thing a publisher wants to know.

HOW THE TOKEN WORKS

A 128-bit random token names a drop; the drop's manifest lives beside the
files, server-side. No signing key, because there is nothing to sign: the token
*is* the capability, and knowing it is the only way in. That means there is
also no secret to leak, rotate, or accidentally commit.

Expiry is checked on every request against the manifest, not against anything
the client sends, so a link cannot be extended by editing it.

WHAT THIS IS NOT
    A CDN, or a public file host. The bundled server is stdlib http.server: it
    is right for a desktop agent on a machine the publisher controls, or a
    small internal box. Putting it on the open internet is a different job with
    a different threat model, and it should sit behind a real reverse proxy if
    it ever goes there.
"""

import json
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import BUILT, ConnectorError, DeliveryChannel, NotConfigured, register

MANIFEST = "manifest.json"
TOKEN_BYTES = 16                 # 128 bits
DEFAULT_TTL_DAYS = 14


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def new_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def read_manifest(drop_dir):
    path = Path(drop_dir) / MANIFEST
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_expired(manifest, now=None):
    """True when a drop has passed its expiry. Unreadable expiry counts as expired."""
    try:
        exp = datetime.fromisoformat(manifest["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (now or _now()) > exp


def record_download(drop_dir, filename, client=""):
    """Append a download to the drop's manifest.

    The telemetry is the point, not a side effect: a count of how often a
    licensed copy was pulled, and from where, is a licensing signal.
    """
    path = Path(drop_dir) / MANIFEST
    manifest = read_manifest(drop_dir)
    if manifest is None:
        return
    manifest.setdefault("downloads", []).append(
        {"file": filename, "at": _iso(_now()), "client": client})
    try:
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass          # telemetry must never break a delivery


@register
class PortalDelivery(DeliveryChannel):
    name = "portal"
    label = "Expiring download link"
    description = ("A per-order link that expires. No attachment limits, "
                   "better deliverability, and download telemetry.")
    state = BUILT

    def __init__(self, root=None, base_url="", ttl_days=DEFAULT_TTL_DAYS):
        self.root = Path(root) if root else None
        self.base_url = (base_url or "").rstrip("/")
        self.ttl_days = int(ttl_days)

    def configure(self, root=None, base_url=None, ttl_days=None, **_ignored):
        if root:
            self.root = Path(root)
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if ttl_days is not None:
            self.ttl_days = int(ttl_days)
        return self

    def health(self):
        if not self.root:
            return False, "No portal folder chosen."
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, "Cannot write to {}: {}".format(self.root, exc)
        if not self.base_url:
            return True, ("{} (no base URL set, links will be relative)"
                          .format(self.root))
        return True, "{} serving at {}".format(self.root, self.base_url)

    def deliver(self, order, files):
        """Publish an order's files under a fresh token. Returns a receipt."""
        if not self.root:
            raise NotConfigured("No portal folder chosen.")
        files = [Path(f) for f in files]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise ConnectorError("Cannot publish files that do not exist: {}"
                                 .format(", ".join(missing[:3])))
        if not files:
            raise ConnectorError("Nothing to deliver for {}"
                                 .format(order.get("order_ref") or "this order"))

        token = new_token()
        drop = self.root / token
        drop.mkdir(parents=True, exist_ok=False)

        stored = []
        for f in files:
            shutil.copy2(f, drop / f.name)
            stored.append(f.name)

        issued = _now()
        expires = issued + timedelta(days=self.ttl_days)
        manifest = {
            "token": token,
            "order_ref": order.get("order_ref", ""),
            "licensee": order.get("buyer", ""),
            "email": order.get("email", ""),
            "item_title": order.get("item_title", ""),
            "files": stored,
            "issued_at": _iso(issued),
            "expires_at": _iso(expires),
            "downloads": [],
        }
        (drop / MANIFEST).write_text(json.dumps(manifest, indent=2),
                                     encoding="utf-8")

        url = "{}/d/{}".format(self.base_url, token) if self.base_url else "/d/" + token
        return {
            "channel": self.name,
            "sent_at": _iso(issued),
            "detail": "{} file(s), link expires {}".format(
                len(stored), expires.date().isoformat()),
            "url": url,
            "token": token,
            "expires_at": _iso(expires),
        }

    # -- housekeeping -------------------------------------------------------

    def drops(self):
        """Every drop under the portal root, newest first."""
        if not self.root or not self.root.is_dir():
            return []
        out = []
        for child in self.root.iterdir():
            if child.is_dir():
                m = read_manifest(child)
                if m:
                    out.append(m)
        out.sort(key=lambda m: m.get("issued_at", ""), reverse=True)
        return out

    def purge_expired(self, now=None):
        """Delete drops past their expiry. Returns how many were removed.

        An expired link that still serves files is not an expiring link.
        """
        removed = 0
        for child in list((self.root or Path(".")).iterdir()):
            if not child.is_dir():
                continue
            m = read_manifest(child)
            if m and is_expired(m, now):
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------

def build_handler(root):
    """An http.server handler that serves drops and nothing else."""
    from http.server import BaseHTTPRequestHandler

    root_path = Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "Opus"
        sys_version = ""

        def log_message(self, fmt, *args):      # quieter than the default
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            raw = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def _page(self, title, body):
            return ("<!doctype html><meta charset=utf-8>"
                    "<meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>{t}</title><style>"
                    "body{{font:15px/1.6 system-ui,sans-serif;max-width:34rem;"
                    "margin:12vh auto;padding:0 1.4rem;color:#14162a;background:#f0f0ed}}"
                    "h1{{font:600 22px/1.25 Palatino,Georgia,serif;margin:0 0 .6rem}}"
                    "a{{color:#9c2b3a}} li{{margin:.35rem 0}}"
                    "code{{background:#e9e9e4;padding:1px 5px;border-radius:3px}}"
                    "</style><h1>{t}</h1>{b}").format(t=title, b=body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            parts = [p for p in self.path.split("?")[0].split("/") if p]

            if not parts:
                return self._send(404, self._page(
                    "Not found", "<p>Nothing here.</p>"))

            if parts[0] != "d" or len(parts) < 2:
                return self._send(404, self._page(
                    "Not found", "<p>Nothing here.</p>"))

            token = parts[1]
            # Token comes off the URL, so it must never reach the filesystem
            # as anything but a single, well-formed path segment.
            if not token.replace("-", "").replace("_", "").isalnum():
                return self._send(400, self._page(
                    "Bad link", "<p>That link is not valid.</p>"))

            drop = (root_path / token).resolve()
            if drop.parent != root_path or not drop.is_dir():
                return self._send(404, self._page(
                    "Link not found",
                    "<p>This link does not exist, or has already expired and "
                    "been cleared.</p>"))

            manifest = read_manifest(drop)
            if manifest is None:
                return self._send(404, self._page(
                    "Link not found", "<p>This link is no longer available.</p>"))

            if is_expired(manifest):
                return self._send(410, self._page(
                    "Link expired",
                    "<p>This download link expired on <code>{}</code>.</p>"
                    "<p>Contact the publisher for a new one.</p>".format(
                        str(manifest.get("expires_at", ""))[:10])))

            if len(parts) == 2:
                items = "".join(
                    '<li><a href="/d/{}/{}">{}</a></li>'.format(
                        token, _url_quote(f), _html(f))
                    for f in manifest.get("files", []))
                return self._send(200, self._page(
                    "Your licensed files",
                    "<p>Licensed to <b>{}</b>.</p><ul>{}</ul>"
                    "<p>These files are stamped with the licensee's name and "
                    "locked against editing. This link expires on "
                    "<code>{}</code>.</p>".format(
                        _html(manifest.get("licensee", "")), items,
                        str(manifest.get("expires_at", ""))[:10])))

            name = _url_unquote(parts[2])
            if name not in manifest.get("files", []):
                return self._send(404, self._page(
                    "Not found", "<p>That file is not part of this delivery.</p>"))

            target = (drop / name).resolve()
            if target.parent != drop or not target.is_file():
                return self._send(404, self._page(
                    "Not found", "<p>That file is not available.</p>"))

            record_download(drop, name, self.client_address[0]
                            if self.client_address else "")
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="{}"'.format(name))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

    return Handler


def _html(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _url_quote(text):
    from urllib.parse import quote
    return quote(str(text))


def _url_unquote(text):
    from urllib.parse import unquote
    return unquote(str(text))


def serve(root, port=8080, host="127.0.0.1", log=print):
    """Run the portal until interrupted. Returns 0."""
    from http.server import ThreadingHTTPServer

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), build_handler(root))
    if log:
        log("Portal serving {} at http://{}:{}/".format(root, host, port))
        log("Links look like http://{}:{}/d/<token>. Ctrl-C to stop.".format(
            host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if log:
            log("\nStopped.")
    finally:
        httpd.server_close()
    return 0
