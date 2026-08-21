#!/usr/bin/env python3
"""Tests for the app interface: settings storage, the JSON API, the server.

    python3 tests/test_webui.py

No pytest, same as the other suites.

Until now the interface was verified by driving a browser by hand, which is
real but not repeatable. These cover the parts a browser test would not reach
anyway: that a password is never sent to the page, that a masked secret coming
back does not overwrite the real one, that the endpoints refuse politely when
nothing is configured, and that every guard on the local server actually holds.

The server tests talk to a real socket. The security guards are the reason this
interface can exist at all -- a localhost API that reads folders and writes
stamped PDFs is reachable by any page the user has open -- so they are tested
against HTTP rather than by calling the handler directly.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webui import api, state                                   # noqa: E402
from webui import server as srv                                # noqa: E402

PASSED, FAILED = [], []
PORT = 7896


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name
          + (("   -> " + str(detail)) if detail and not condition else ""))


def section(title):
    print("\n" + title)
    print("-" * len(title))


class TempConfig:
    """Point state at a throwaway file, so tests never touch the real one."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())

    def __enter__(self):
        self._path = state.config_path
        self._dir = state.config_dir
        state.config_path = lambda: self.dir / "config.json"
        state.config_dir = lambda: self.dir
        api.state.config_path = state.config_path
        api.state.config_dir = state.config_dir
        return self

    def __exit__(self, *_exc):
        state.config_path = self._path
        state.config_dir = self._dir
        api.state.config_path = self._path
        api.state.config_dir = self._dir
        shutil.rmtree(self.dir, ignore_errors=True)


def configured(out_dir=None):
    """A config pointing at the repository's own sample data."""
    cfg = state.load()
    cfg["paths"]["catalog_root"] = str(ROOT / "samples" / "catalog")
    cfg["paths"]["paypal_csv"] = str(ROOT / "examples" / "paypal_sample.csv")
    if out_dir:
        cfg["paths"]["out_dir"] = str(out_dir)
    return cfg


def call(method, path, body=None, config=None):
    return api.dispatch(method, path, body, config if config is not None
                        else state.load())


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def test_state():
    section("Settings storage")
    with TempConfig():
        cfg = state.load()
        check("a missing config yields defaults", cfg["schema"] == 1)
        check("automation starts held", cfg["review"]["hold_below"] > 1.0,
              cfg["review"]["hold_below"])

        cfg["publisher"] = "Fictitious Editions"
        cfg["connectors"]["smtp"]["password"] = "hunter2"
        path = state.save(cfg)
        check("saving writes a file", path.is_file())

        again = state.load()
        check("settings survive a reload", again["publisher"] == "Fictitious Editions")
        check("secrets are stored", again["connectors"]["smtp"]["password"] == "hunter2")

        # a config from an older version must still work
        thin = {"schema": 1, "publisher": "Old"}
        state.config_path().write_text(json.dumps(thin), encoding="utf-8")
        merged = state.load()
        check("an older config is merged over the defaults",
              merged["publisher"] == "Old" and "dashboard" in merged)

        state.config_path().write_text("{not json", encoding="utf-8")
        check("a corrupt config falls back rather than refusing to start",
              state.load()["schema"] == 1)


def test_redaction():
    section("Secrets never reach the browser")
    with TempConfig():
        cfg = state.load()
        cfg["connectors"]["smtp"]["password"] = "hunter2"
        cfg["connectors"]["paypal-api"]["client_secret"] = "sk_live_abc"
        state.save(cfg)

        _s, payload = call("GET", "/api/settings", None, cfg)
        sent = json.dumps(payload)
        check("the real password is not in the response", "hunter2" not in sent)
        check("nor the api secret", "sk_live_abc" not in sent)
        check("they appear as a mask",
              payload["settings"]["connectors"]["smtp"]["password"] == state.MASK)
        check("the mask hides the length", state.MASK == "*" * 8)
        check("an unset secret stays empty",
              payload["settings"]["connectors"]["gdrive"].get("key_file", "") == "")

        # the UI round-trips whatever it was given
        _s, out = call("POST", "/api/settings", {
            "section": "connectors",
            "values": {"smtp": {"password": state.MASK, "host": "mail.example.org"}}
        }, cfg)
        check("a masked secret coming back does not overwrite the real one",
              cfg["connectors"]["smtp"]["password"] == "hunter2")
        check("the rest of the patch still applies",
              cfg["connectors"]["smtp"]["host"] == "mail.example.org")

        _s, _o = call("POST", "/api/settings", {
            "section": "connectors",
            "values": {"smtp": {"password": "newsecret"}}}, cfg)
        check("a real new secret does replace it",
              cfg["connectors"]["smtp"]["password"] == "newsecret")


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

def test_api_unconfigured():
    section("The API refuses politely before setup")
    with TempConfig():
        cfg = state.load()
        status, payload = call("GET", "/api/status", None, cfg)
        check("status always answers", status == 200)
        check("and reports not ready", payload["ready"] is False)

        for path in ("/api/orders", "/api/catalog"):
            status, payload = call("GET", path, None, cfg)
            check("{} says what is missing".format(path), status == 409, status)
            check("{} explains how to fix it".format(path),
                  bool(payload.get("detail")), payload)

        status, payload = call("GET", "/api/ledger", None, cfg)
        check("an empty ledger is not an error", status == 200 and
              payload["ledger"] == [])

        status, _p = call("POST", "/api/run", {"confirm": True}, cfg)
        check("running without an output folder is refused", status == 409)

        status, _p = call("GET", "/api/nope", None, cfg)
        check("an unknown endpoint is a 404", status == 404)

        status, _p = call("POST", "/api/settings", {"section": "evil"}, cfg)
        check("an unknown settings section is rejected", status == 400)


def test_api_configured():
    section("The API against the repository's own sample data")
    with TempConfig():
        out = Path(tempfile.mkdtemp())
        cfg = configured(out)

        status, payload = call("GET", "/api/status", None, cfg)
        check("reports ready", payload["ready"] is True, payload)
        check("counts the catalogue", payload["catalog"]["pieces"] == 2
              and payload["catalog"]["parts"] == 6, payload["catalog"])

        status, payload = call("GET", "/api/orders", None, cfg)
        check("four orders", payload["counts"]["total"] == 4, payload["counts"])
        check("the default posture releases none",
              payload["counts"]["release"] == 0, payload["counts"])
        first = payload["orders"][0]
        for field in ("order_ref", "buyer", "score", "verdict", "signals", "match"):
            check("orders carry {}".format(field), field in first)
        check("signals are explainable",
              all(s.get("note") for s in first["signals"]))

        status, payload = call("POST", "/api/threshold-preview",
                               {"hold_below": 0.80}, cfg)
        check("the dial preview releases three at 0.80",
              payload["counts"]["release"] == 3, payload["counts"])
        status, payload = call("POST", "/api/threshold-preview",
                               {"hold_below": 0.95}, cfg)
        check("and none at 0.95", payload["counts"]["release"] == 0,
              payload["counts"])
        status, _p = call("POST", "/api/threshold-preview",
                          {"hold_below": "banana"}, cfg)
        check("a nonsense threshold is rejected", status == 400)

        check("previewing changed nothing",
              cfg["review"]["hold_below"] > 1.0, cfg["review"]["hold_below"])

        status, payload = call("GET", "/api/catalog", None, cfg)
        check("catalogue lists both pieces", len(payload["catalog"]) == 2)
        check("with page counts",
              all(p["pages"] > 0 for piece in payload["catalog"]
                  for p in piece["parts"]))

        status, payload = call("GET", "/api/connectors", None, cfg)
        names = {c["name"] for c in payload["connectors"]}
        check("every connector is listed", len(payload["connectors"]) == 13,
              len(payload["connectors"]))
        check("built ones carry form fields",
              all(c["fields"] for c in payload["connectors"]
                  if c["name"] in ("local", "smtp", "portal")))
        check("planned ones carry none",
              not any(c["fields"] for c in payload["connectors"]
                      if c["state"] == "planned"))
        local = [c for c in payload["connectors"] if c["name"] == "local"][0]
        check("a connector inherits the folder chosen in Settings",
              local["values"]["root"] == cfg["paths"]["catalog_root"],
              local["values"])

        status, payload = call("POST", "/api/connectors/test",
                               {"kind": "catalog", "name": "local"}, cfg)
        check("Test reaches the adapter's own health check",
              payload["ok"] is True, payload)
        status, payload = call("POST", "/api/connectors/test",
                               {"kind": "delivery", "name": "outlook"}, cfg)
        check("testing a planned connector fails honestly",
              payload["ok"] is False and "not built" in payload["message"].lower(),
              payload)
        status, payload = call("POST", "/api/connectors/test",
                               {"kind": "order", "name": "nope"}, cfg)
        check("an unknown connector is a 404", status == 404)

        shutil.rmtree(out, ignore_errors=True)


def test_api_run():
    section("Running a batch through the API")
    with TempConfig():
        out = Path(tempfile.mkdtemp())
        cfg = configured(out)
        cfg["review"]["hold_below"] = 0.80
        state.save(cfg)

        status, _p = call("POST", "/api/run", {}, cfg)
        check("a run must be confirmed", status == 409)
        check("nothing was written", not list(out.glob("*.pdf")))

        status, payload = call("POST", "/api/run", {"confirm": True}, cfg)
        check("the run succeeds", status == 200, payload)
        check("three orders", payload["summary"]["orders"] == 3, payload["summary"])
        check("nine files", payload["summary"]["files_ok"] == 9, payload["summary"])
        check("progress is reported per file", len(payload["progress"]) == 9)
        check("the ledger chain is checked after writing",
              payload["ledger_intact"] is True, payload["ledger_report"])
        check("the files exist", len(list(out.glob("*.pdf"))) == 9)

        status, payload = call("GET", "/api/ledger", None, cfg)
        check("the ledger endpoint sees them", len(payload["ledger"]) == 9)
        check("rows carry the decision",
              all(r["decision"] == "release" for r in payload["ledger"]))
        check("rows carry the score", all(r["confidence"] for r in payload["ledger"]))
        check("nothing claims to have been delivered",
              not any(r["delivered_at"] for r in payload["ledger"]))

        status, payload = call("POST", "/api/ledger/verify", None, cfg)
        check("verify agrees", payload["intact"] is True, payload)

        # a second pass must not reissue
        status, payload = call("GET", "/api/orders", None, cfg)
        check("already-issued orders are held on the next pass",
              payload["counts"]["release"] == 0, payload["counts"])

        shutil.rmtree(out, ignore_errors=True)


def test_api_customisation():
    section("Dashboard, views and delivery settings")
    with TempConfig():
        cfg = state.load()

        widgets = [{"id": "recent", "visible": True},
                   {"id": "tiles", "visible": False}]
        status, payload = call("POST", "/api/dashboard",
                               {"values": {"widgets": widgets, "theme": "dark",
                                           "density": "compact"}}, cfg)
        check("dashboard layout saves", status == 200 and payload["saved"])
        check("it persists to disk",
              state.load()["dashboard"]["theme"] == "dark")
        check("order is kept",
              [w["id"] for w in state.load()["dashboard"]["widgets"]][0] == "recent")
        check("hidden stays hidden",
              state.load()["dashboard"]["widgets"][1]["visible"] is False)

        views = [{"id": "v1", "name": "Held orders", "workspace": "ledger",
                  "query": "delgado", "filters": {"status": "held"}}]
        status, payload = call("POST", "/api/views", {"views": views}, cfg)
        check("views save", payload["saved"] and len(payload["views"]) == 1)
        check("and reload", len(call("GET", "/api/views", None, state.load())[1]["views"]) == 1)

        status, _p = call("POST", "/api/views", {"views": "not a list"}, cfg)
        check("a malformed views payload is rejected", status == 400)

        many = [{"id": str(i), "name": "v%d" % i} for i in range(200)]
        _s, payload = call("POST", "/api/views", {"views": many}, cfg)
        check("the view list is capped", len(payload["views"]) <= 50,
              len(payload["views"]))

        long_name = {"id": "x", "name": "n" * 500, "query": "q" * 500}
        _s, payload = call("POST", "/api/views", {"views": [long_name]}, cfg)
        check("long fields are truncated rather than stored whole",
              len(payload["views"][0]["name"]) <= 80
              and len(payload["views"][0]["query"]) <= 200)

        _s, _p = call("POST", "/api/settings", {"section": "review", "values": {
            "auto_deliver": True, "deliver_channels": ["portal"]}}, cfg)
        check("delivery settings save", state.load()["review"]["auto_deliver"] is True)

        # delivery on, but the portal has nowhere to write
        cfg = configured(Path(tempfile.mkdtemp()))
        cfg["review"]["auto_deliver"] = True
        cfg["review"]["deliver_channels"] = ["portal"]
        status, payload = call("POST", "/api/run", {"confirm": True}, cfg)
        check("a run refuses when delivery is on but unconfigured",
              status == 409 and "portal" in payload["error"].lower(), payload)


def test_api_browse():
    section("The folder picker")
    with TempConfig():
        cfg = state.load()
        status, payload = call("POST", "/api/browse", {"path": str(ROOT)}, cfg)
        check("lists a folder", status == 200 and payload["entries"])
        check("offers a way up", bool(payload["parent"]))
        kinds = {e["kind"] for e in payload["entries"]}
        check("only folders and openable files are offered",
              kinds <= {"dir", "pdf", "csv", "json"}, kinds)
        names = {e["name"] for e in payload["entries"]}
        check("hidden files are not listed",
              not any(n.startswith(".") for n in names))
        check("opus.py is not offered as a choice", "opus.py" not in names)

        status, payload = call("POST", "/api/browse",
                               {"path": str(ROOT / "opus.py")}, cfg)
        check("pointing at a file lands in its folder",
              Path(payload["path"]) == ROOT, payload["path"])

        status, payload = call("POST", "/api/browse",
                               {"path": "/nonexistent-nowhere-12345"}, cfg)
        check("a missing path falls back to somewhere real",
              status == 200 and Path(payload["path"]).is_dir(), payload)


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

class LiveServer:
    def __init__(self, port):
        self.session = srv.Session()
        self.port = port
        # The same server class the app runs, so its error handling is
        # covered rather than bypassed.
        self.httpd = srv.QuietServer(
            ("127.0.0.1", port), srv.build_handler(self.session, port))

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.25)
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, path, token=None, host=None, origin=None,
                method="GET", body=None):
        url = "http://127.0.0.1:{}{}".format(self.port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token:
            req.add_header("X-Opus-Token", token)
        if host:
            req.add_header("Host", host)
        if origin:
            req.add_header("Origin", origin)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)


def test_server_guards():
    section("The local server's guards")
    with TempConfig(), LiveServer(PORT) as s:
        token = s.session.token
        check("the token is long enough to be unguessable", len(token) >= 32,
              len(token))

        check("no token is refused", s.request("/api/status")[0] == 401)
        check("a wrong token is refused",
              s.request("/api/status", token="nope")[0] == 401)
        check("the right token works",
              s.request("/api/status", token=token)[0] == 200)

        code, body, _h = s.request("/", token=None)
        check("the page without a token explains itself", code == 401
              and b"session key" in body)

        check("a spoofed Host is refused (DNS rebinding)",
              s.request("/api/status", token=token, host="evil.example")[0] == 403)
        check("a cross-origin caller is refused",
              s.request("/api/status", token=token,
                        origin="https://evil.example")[0] == 403)
        check("the app's own origin is allowed",
              s.request("/api/status", token=token,
                        origin="http://127.0.0.1:{}".format(PORT))[0] == 200)

        for attack in ("/../opus.py", "/..%2fopus.py", "/static/../../opus.py",
                       "/%2e%2e/opus.py"):
            check("traversal refused: {}".format(attack),
                  s.request(attack, token=token)[0] == 404)

        check("a GET on a POST-only route is a 404",
              s.request("/api/run", token=token)[0] == 404)
        check("a POST to a non-api path is a 404",
              s.request("/nope", token=token, method="POST", body={})[0] == 404)

        # The client has to actually receive the 413 rather than a connection
        # reset, which is what happens if the server answers without draining
        # the body it is refusing. macOS surfaces that; Windows tolerated it.
        big = {"x": "a" * (srv.MAX_BODY + 32)}
        code, body, _h = s.request("/api/settings", token=token,
                                   method="POST", body=big)
        check("an oversized body is refused", code == 413, code)
        check("and the client receives the message, not a reset",
              b"too large" in body, body[:80])

        code, _b, _h = s.request("/api/settings", token=token, method="POST",
                                 body=None)
        check("an empty POST body is handled", code in (200, 400), code)

        code, _b, headers = s.request("/", token=token)
        check("the app page is served", code == 200)
        check("it sets the token as a cookie", "Set-Cookie" in headers)
        check("the cookie is not readable by scripts",
              "HttpOnly" in headers.get("Set-Cookie", ""))
        check("and does not travel cross-site",
              "SameSite=Strict" in headers.get("Set-Cookie", ""))
        check("a content security policy is set",
              "default-src 'self'" in headers.get("Content-Security-Policy", ""))
        check("the CSP allows no remote anything",
              "http" not in headers.get("Content-Security-Policy", "")
              .replace("'self'", ""))
        check("sniffing is disabled",
              headers.get("X-Content-Type-Options") == "nosniff")

        for asset, ctype in (("/app.css", "text/css"), ("/app.js", "javascript")):
            code, body, headers = s.request(asset, token=token)
            check("{} is served".format(asset), code == 200 and len(body) > 100)
            check("{} has the right type".format(asset),
                  ctype in headers.get("Content-Type", ""),
                  headers.get("Content-Type"))


def test_server_is_local_only():
    section("The server is not reachable from the network")
    import socket
    with TempConfig(), LiveServer(PORT + 1):
        try:
            addr = socket.gethostbyname(socket.gethostname())
        except OSError:
            addr = ""
        if not addr or addr.startswith("127."):
            check("no external address to test against (skipped)", True)
            return
        sock = socket.socket()
        sock.settimeout(1.5)
        try:
            sock.connect((addr, PORT + 1))
            check("the external interface refuses the connection", False,
                  "reachable at {}".format(addr))
        except OSError:
            check("the external interface refuses the connection", True)
        finally:
            sock.close()


def test_interface_files_exist():
    section("The interface the server serves")
    check("app.html is present", (srv.STATIC / "app.html").is_file())
    check("app.css is present", (srv.STATIC / "app.css").is_file())
    check("app.js is present", (srv.STATIC / "app.js").is_file())

    html = (srv.STATIC / "app.html").read_text(encoding="utf-8")
    for ws in ("overview", "orders", "catalog", "ledger", "conn", "settings"):
        check("workspace {} exists in the markup".format(ws),
              'id="ws-{}"'.format(ws) in html)
    check("the interface is not inlined (the demo build depends on the links)",
          'href="app.css"' in html and 'src="app.js"' in html)

    js = (srv.STATIC / "app.js").read_text(encoding="utf-8")
    check("the API adapter is swappable, which the demo build relies on",
          "window.OPUS_MOCK" in js)
    # There are two fetch calls, one per verb, and both must live inside the
    # adapter. Anything below it talking to the network directly would work in
    # the app and silently break the static demo.
    adapter_end = js.index("function readJson")
    check("every fetch call lives inside the swappable adapter",
          "fetch(" not in js[adapter_end:], js[adapter_end:].count("fetch("))
    check("both verbs go through it", js[:adapter_end].count("fetch(") == 2,
          js[:adapter_end].count("fetch("))


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------

def test_help():
    section("What Opus says about itself")
    with TempConfig():
        cfg = state.load()
        _s, payload = call("GET", "/api/help", None, cfg)
        ws = payload["workspaces"]
        check("every workspace is documented", len(ws) >= 6, len(ws))
        check("the threshold has its own entry", "threshold" in ws)
        for key, entry in ws.items():
            check("{} answers all three questions".format(key),
                  all(entry.get(k) for k in ("what", "decide", "watch")))
            check("{} is written in sentences".format(key),
                  entry["what"].endswith(".") and len(entry["what"]) > 40)
        check("the threshold entry says what she is committing to",
              "without you" in ws["threshold"]["decide"], ws["threshold"]["decide"])
        check("and names the two hard holds",
              "already in the ledger" in ws["threshold"]["watch"])
        check("the ledger entry explains why the chain matters",
              "evidence" in ws["ledger"]["watch"])
        check("no entry describes the layout instead of the concept",
              not any("click the" in e["what"].lower() for e in ws.values()))


def test_first_run_steps():
    section("The checklist reports from real settings")
    with TempConfig():
        cfg = state.load()
        _s, payload = call("GET", "/api/help", None, cfg)
        steps = payload["steps"]
        check("there are five steps", len(steps) == 5, len(steps))
        check("nothing is done on a fresh install",
              not any(x["done"] for x in steps))
        check("practice comes first", steps[0]["id"] == "practice")
        check("each step says what to press", all(x["action"] for x in steps))
        check("each step says where to go", all(x["workspace"] for x in steps))

        call("POST", "/api/practice", {"on": True}, cfg)
        _s, payload = call("GET", "/api/help", None, cfg)
        by_id = {x["id"]: x for x in payload["steps"]}
        check("turning practice on ticks step one", by_id["practice"]["done"])
        check("and the catalogue step, since practice supplies one",
              by_id["catalog"]["done"])
        check("but not the ones genuinely undone",
              not by_id["out"]["done"] and not by_id["review"]["done"])

        call("POST", "/api/practice", {"on": False}, cfg)
        _s, payload = call("GET", "/api/help", None, cfg)
        check("switching back un-ticks them",
              not any(x["done"] for x in payload["steps"]))


def test_practice_mode():
    section("Practice mode")
    with TempConfig():
        cfg = state.load()
        cfg["paths"]["catalog_root"] = "/her/own/music"
        cfg["paths"]["out_dir"] = "/her/own/licensed"

        _s, payload = call("POST", "/api/practice", {"on": True}, cfg)
        check("it turns on", payload["practice"] is True)
        check("the app becomes usable immediately",
              payload["status"]["ready"] is True, payload["status"])
        check("using the bundled catalogue",
              payload["status"]["catalog"]["pieces"] == 2)
        check("her own folders are not overwritten",
              cfg["paths"]["catalog_root"] == "/her/own/music", cfg["paths"])

        _s, orders = call("GET", "/api/orders", None, cfg)
        check("a made-up day of orders is there",
              orders["counts"]["total"] == 4, orders["counts"])

        _s, run = call("POST", "/api/run", {"confirm": True}, cfg)
        check("a whole batch runs safely", run["summary"]["orders"] >= 0,
              run["summary"])
        practice_dir = Path(api.practice_paths()["out_dir"])
        check("output goes to the practice folder",
              practice_dir.exists() and "/her/own" not in str(practice_dir),
              str(practice_dir))

        _s, payload = call("POST", "/api/practice", {"on": False}, cfg)
        check("it turns off", payload["practice"] is False)
        check("her own folders come back untouched",
              cfg["paths"]["catalog_root"] == "/her/own/music")

        shutil.rmtree(practice_dir, ignore_errors=True)


def test_practice_threshold_does_not_leak():
    section("A threshold chosen while practising stays there")
    with TempConfig():
        cfg = state.load()
        check("it starts holding everything", cfg["review"]["hold_below"] > 1.0)

        call("POST", "/api/practice", {"on": True}, cfg)
        call("POST", "/api/settings",
             {"section": "review", "values": {"hold_below": 0.5}}, cfg)
        check("she can lower it while practising",
              cfg["review"]["hold_below"] == 0.5)

        _s, payload = call("POST", "/api/practice", {"on": False}, cfg)
        check("leaving practice restores the real one",
              cfg["review"]["hold_below"] > 1.0, cfg["review"]["hold_below"])
        check("and she is told it happened",
              payload["threshold_restored"]["to"] > 1.0,
              payload.get("threshold_restored"))
        check("the bookkeeping does not linger in the config",
              "hold_below_before_practice" not in cfg["review"])

        call("POST", "/api/settings",
             {"section": "review", "values": {"hold_below": 0.9}}, cfg)
        call("POST", "/api/practice", {"on": True}, cfg)
        _s, payload = call("POST", "/api/practice", {"on": False}, cfg)
        check("a threshold set outside practice survives",
              cfg["review"]["hold_below"] == 0.9, cfg["review"]["hold_below"])
        check("and nothing is announced when nothing changed",
              payload["threshold_restored"] is None)


def test_onboarding_dismiss():
    section("Dismissing the checklist")
    with TempConfig():
        cfg = state.load()
        _s, status = call("GET", "/api/status", None, cfg)
        check("it starts visible", status["onboarding_dismissed"] is False)
        _s, payload = call("POST", "/api/onboarding", {"dismissed": True}, cfg)
        check("it can be dismissed", payload["onboarding"]["dismissed"] is True)
        check("that persists", state.load()["onboarding"]["dismissed"] is True)
        _s, status = call("GET", "/api/status", None, cfg)
        check("status reports it", status["onboarding_dismissed"] is True)


def test_help_reaches_the_interface():
    section("The interface offers the help")
    html = (srv.STATIC / "app.html").read_text(encoding="utf-8")
    from webui import help as help_content
    for key in help_content.WORKSPACES:
        check("a help button exists for {}".format(key),
              'data-help="{}"'.format(key) in html)
    check("practice mode has a banner", 'id="practicebanner"' in html)
    check("the checklist has a home", 'id="ws-start"' in html)
    js = (srv.STATIC / "app.js").read_text(encoding="utf-8")
    check("the checklist is a dashboard panel too", "renderStartWidget" in js)
    check("the dial says something when there is nothing to preview",
          "nothing to preview against" in js)


def main():
    print("Opus interface tests -- settings, API, server")
    print("=" * 62)
    for fn in (test_state, test_redaction, test_api_unconfigured,
               test_api_configured, test_api_run, test_api_customisation,
               test_api_browse, test_server_guards, test_server_is_local_only,
               test_interface_files_exist, test_help, test_first_run_steps,
               test_practice_mode, test_practice_threshold_does_not_leak,
               test_onboarding_dismiss, test_help_reaches_the_interface):
        fn()

    print("\n" + "=" * 62)
    print("{} passed, {} failed".format(len(PASSED), len(FAILED)))
    for name in FAILED:
        print("  FAILED: {}".format(name))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
