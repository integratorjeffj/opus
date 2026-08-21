#!/usr/bin/env python3
"""Build the public demo out of the application's own interface.

    pip3 install Pillow pypdfium2
    python3 packaging/build_demo.py

Pillow and pypdfium2 are needed only to render the page crop shown in the
drawer; the application itself never imports either.

docs/demo/index.html is generated, never hand-edited. It inlines the real
app.html, app.css and app.js from webui/static and prepends a mock adapter that
answers the same endpoints the local server does, from data captured by running
the real engine against the fictional catalogue in this repository.

WHY IT IS BUILT THIS WAY

For a while the demo and the app were separate files. The demo got the design
attention and the app got the features, and they drifted until the polished one
could not do anything and the capable one looked like 1998. Generating the demo
from the app makes that drift impossible: a workspace added to the interface
appears here automatically, and if this build breaks, the interface changed in
a way somebody needs to look at.

The mock is honest about being a mock. Writes are accepted and remembered for
the session so the page feels alive, but nothing is stamped and nothing is
sent, and the banner says so before anyone touches a control.
"""

import base64
import csv
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webui" / "static"
OUT = ROOT / "docs" / "demo" / "index.html"

IMG_QUALITY = 86
DEMO_HOLD_BELOW = 0.80

ISSUED = (ROOT / "samples" / "licensed"
          / "Evening_Bells_score__First_Baptist_Church_Springfield.pdf")

sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def render_notice(pdf_path, password=None):
    """A crop of the stamped band, legible at the size a browser shows it.

    At full-page thumbnail width an 8pt notice renders about four pixels tall,
    which defeats the entire point of showing it.
    """
    import pypdfium2 as pdfium
    from PIL import Image

    kwargs = {"password": password} if password is not None else {}
    doc = pdfium.PdfDocument(str(pdf_path), **kwargs)
    pil = doc[0].render(scale=4.0).to_pil().convert("RGB")
    w, h = pil.size
    pil = pil.crop((0, 0, w, int(h * 0.085)))
    pil = pil.resize((1000, max(1, int(pil.size[1] * 1000 / pil.size[0]))),
                     Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=IMG_QUALITY, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

def read_version():
    src = (ROOT / "opus.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    return m.group(1) if m else "0.0.0"


def demo_values(name):
    """Plausible, obviously-fictional settings so the forms are not empty."""
    return {
        "local": {"root": "~/Music/Catalogue"},
        "paypal-csv": {"path": "~/Downloads/activity.csv"},
        "portal": {"root": "~/Music/Portal", "base_url": "", "ttl_days": 14},
        "smtp": {"port": 587},
    }.get(name, {})


def capture():
    """Run the real engine and record what each endpoint would answer."""
    import opus
    import connectors
    from connectors import confidence as conf
    from connectors.catalog_local import collect_pdfs, scan_catalog, write_catalog_map
    from webui.api import _fields_for

    cat_map = write_catalog_map(ROOT / "samples" / "catalog",
                                Path(tempfile.mkdtemp()) / "catalog_map.csv")
    catalog = opus.load_catalog(cat_map)
    orders, warnings = opus.read_paypal_orders(
        ROOT / "examples" / "paypal_sample.csv")
    plan = opus.plan_paypal_batch(orders, catalog, None)
    assessed = conf.assess_plan(plan, catalog, hold_below=DEMO_HOLD_BELOW)

    def entry(e, a):
        return {
            "order_ref": e.get("order_ref", ""), "buyer": e.get("buyer", ""),
            "email": e.get("email", ""),
            "date": opus.format_date(e["order_date"]),
            "item": e.get("item_title", ""), "matched": e.get("matched_title", ""),
            "match": e.get("match", "none"),
            "files": len(e.get("files") or []),
            "parts": [Path(str(f)).name for f in (e.get("files") or [])],
            "status": e.get("disposition", ""),
            "score": round(a.score, 2), "verdict": a.verdict,
            "signals": [s.as_dict() for s in a.signals],
            "reasons": list(a.reasons),
        }

    counts = conf.summarize(assessed)
    orders_payload = {
        "orders": [entry(e, a) for e, a in assessed],
        "warnings": warnings, "hold_below": DEMO_HOLD_BELOW,
        "counts": {"release": counts.get("release", 0),
                   "hold": counts.get("hold", 0),
                   "reject": counts.get("reject", 0),
                   "total": len(assessed)},
    }

    pieces = []
    for item in scan_catalog(ROOT / "samples" / "catalog"):
        parts = []
        for pdf in collect_pdfs(Path(item.ref)):
            try:
                from pypdf import PdfReader
                pages = len(PdfReader(str(pdf)).pages)
            except Exception:
                pages = 0
            parts.append({"name": pdf.name, "pages": pages})
        pieces.append({"title": item.title, "files": item.file_count,
                       "parts": parts})

    led_path = ROOT / "samples" / "licensed" / "license_ledger.csv"
    rows = list(csv.DictReader(led_path.open(encoding="utf-8-sig")))
    intact, report = opus.verify_ledger(led_path)
    ledger = [{
        "licensee": r.get("licensee", ""), "order_ref": r.get("order_ref", ""),
        "item_title": r.get("item_title", ""),
        "file": Path((r.get("output_file") or "").replace("\\", "/")).name,
        "source": Path((r.get("source_file") or "").replace("\\", "/")).name,
        "pages": r.get("pages", ""), "password": r.get("owner_password", ""),
        "status": r.get("status", ""), "confidence": r.get("confidence", ""),
        "decision": r.get("decision", ""),
        "delivered_at": r.get("delivered_at", ""),
        "delivery_channel": r.get("delivery_channel", ""),
        "delivery_ref": r.get("delivery_ref", ""),
        "stamped_at": r.get("stamped_at", ""), "notes": r.get("notes", ""),
    } for r in rows]

    conn = [{"kind": kind, "name": name, "label": label, "state": cstate,
             "description": desc, "configured": True,
             "values": demo_values(name), "fields": _fields_for(name)}
            for kind, name, label, cstate, desc in connectors.describe()]

    version = read_version()
    settings = {
        "schema": 1,
        "paths": {"catalog_root": "~/Music/Catalogue", "catalog_map": "",
                  "paypal_csv": "~/Downloads/activity.csv",
                  "out_dir": "~/Music/Licensed", "portal_root": "",
                  "watch_folder": ""},
        "connectors": {}, "publisher": "Fictitious Editions",
        "review": {"hold_below": DEMO_HOLD_BELOW, "auto_deliver": False,
                   "deliver_channels": []},
        "dashboard": {"widgets": [{"id": w, "visible": True} for w in
                                  ("tiles", "attention", "queue", "dropped",
                                   "recent")],
                      "theme": "system", "density": "comfortable"},
        "views": [],
    }

    return {
        "settings": settings,
        "config_path": "nothing is saved in the demo",
        "version": version,
        "status": {
            "version": version, "ready": True,
            "catalog": {"ready": True, "pieces": len(pieces),
                        "parts": sum(p["files"] for p in pieces),
                        "root": "~/Music/Catalogue"},
            "export": {"ready": True, "path": "~/Downloads/activity.csv"},
            "out_dir": "~/Music/Licensed",
            "issued": len([r for r in ledger if r["status"] == "ok"]),
            "ledger_intact": intact, "hold_below": DEMO_HOLD_BELOW, "today": "",
        },
        "orders": orders_payload,
        "catalog": {"catalog": pieces, "root": "~/Music/Catalogue"},
        "ledger": {"ledger": ledger, "intact": intact, "report": report,
                   "path": "~/Music/Licensed/license_ledger.csv"},
        "connectors": {"connectors": conn},
        "images": {"notice": render_notice(ISSUED, password="")},
    }


# ---------------------------------------------------------------------------
# the mock adapter
# ---------------------------------------------------------------------------

MOCK_JS = r"""
/* Generated by packaging/build_demo.py.

   Answers the same endpoints the local server does, from data captured by
   running the real engine. Writes are remembered for the session so the page
   behaves like the app; nothing is stamped and nothing is sent. */
(function () {
  var D = __CAPTURED__;
  var mem = JSON.parse(JSON.stringify(D.settings));
  var views = [];
  var ran = false;

  function copy(v) { return JSON.parse(JSON.stringify(v)); }
  function reply(v) { return Promise.resolve(copy(v)); }

  function scored(threshold) {
    var payload = copy(D.orders);
    var counts = {release: 0, hold: 0, reject: 0, total: payload.orders.length};
    payload.orders.forEach(function (o) {
      if (o.verdict !== "reject") {
        o.verdict = o.score >= threshold ? "release" : "hold";
      }
      counts[o.verdict] += 1;
    });
    payload.counts = counts;
    payload.hold_below = threshold;
    return payload;
  }

  window.OPUS_MOCK = {
    live: false,
    images: D.images,

    get: function (path) {
      if (path === "/api/settings") {
        return reply({settings: mem, config_path: D.config_path,
                      version: D.version});
      }
      if (path === "/api/status") {
        var s = copy(D.status);
        s.hold_below = mem.review.hold_below;
        if (!ran) { s.issued = 0; s.ledger_intact = null; }
        return reply(s);
      }
      if (path === "/api/orders") { return reply(scored(mem.review.hold_below)); }
      if (path === "/api/catalog") { return reply(D.catalog); }
      if (path === "/api/connectors") { return reply(D.connectors); }
      if (path === "/api/views") { return reply({views: views}); }
      if (path === "/api/ledger") {
        if (!ran) {
          return reply({ledger: [], intact: null,
                        report: ["Nothing issued yet."]});
        }
        return reply(D.ledger);
      }
      return Promise.reject(new Error("Not part of the demo."));
    },

    post: function (path, body) {
      body = body || {};

      if (path === "/api/threshold-preview") {
        var p = scored(body.hold_below);
        return reply({hold_below: body.hold_below, counts: p.counts,
                      orders: p.orders.map(function (o) {
                        return {order_ref: o.order_ref, buyer: o.buyer,
                                score: o.score, verdict: o.verdict};
                      })});
      }
      if (path === "/api/settings") {
        if (body.section === "publisher") { mem.publisher = body.values; }
        else if (body.section && mem[body.section]) {
          Object.keys(body.values || {}).forEach(function (k) {
            mem[body.section][k] = body.values[k];
          });
        }
        return reply({settings: mem, saved: true});
      }
      if (path === "/api/dashboard") {
        Object.keys(body.values || {}).forEach(function (k) {
          mem.dashboard[k] = body.values[k];
        });
        return reply({dashboard: mem.dashboard, saved: true});
      }
      if (path === "/api/views") {
        views = body.views || [];
        return reply({views: views, saved: true});
      }
      if (path === "/api/ledger/verify") {
        return reply(ran
          ? {intact: D.ledger.intact, report: D.ledger.report}
          : {intact: null, report: ["Nothing issued yet."]});
      }
      if (path === "/api/connectors/test") {
        var c = D.connectors.connectors.filter(function (x) {
          return x.name === body.name;
        })[0] || {};
        if (c.state === "planned") {
          return reply({ok: false,
                        message: c.label + " is not built yet, so there is " +
                                 "nothing to test."});
        }
        return reply({ok: true,
                      message: "In the app this really reaches " + c.label +
                               " and reports what it found. The demo has no " +
                               "network."});
      }
      if (path === "/api/browse") {
        return reply({path: "~/Music", parent: "", home: "~", entries: [
          {name: "Catalogue", path: "~/Music/Catalogue", kind: "dir"},
          {name: "Licensed", path: "~/Music/Licensed", kind: "dir"},
          {name: "activity.csv", path: "~/Music/activity.csv", kind: "csv"}
        ]});
      }
      if (path === "/api/run") {
        ran = true;
        var issued = D.ledger.ledger.filter(function (r) {
          return r.status === "ok";
        });
        return reply({
          summary: {orders: 3, files_ok: issued.length, files_failed: 0,
                    held: 0, skipped: 1, delivered: 0},
          progress: issued.map(function (r, i) {
            return "[" + (i + 1) + "/" + issued.length + "] " + r.licensee +
                   " - " + r.source;
          }),
          ledger: D.ledger.path, ledger_intact: D.ledger.intact,
          ledger_report: D.ledger.report, warnings: []
        });
      }
      return Promise.reject(new Error("Not part of the demo."));
    }
  };
})();
"""

BANNER = """
      <p class="banner">
        <span class="ic" aria-hidden="true">&#9888;</span>
        <span><b>This is the real interface, running on captured data.</b> The
        same HTML, CSS and JavaScript the desktop app serves &mdash; only the
        data layer is swapped. Nothing is uploaded, nothing is stamped and
        nothing is sent from this page. Every order, score, filename and ledger
        row came from actually running Opus against the fictional catalogue in
        the repository.
        <a href="https://github.com/integratorjeffj/opus">See the code</a>.</span>
      </p>
"""


def build():
    for f in ("app.html", "app.css", "app.js"):
        if not (STATIC / f).is_file():
            sys.exit("Missing {} — the demo is built from the app.".format(f))

    html = (STATIC / "app.html").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    data = capture()
    mock = MOCK_JS.replace("__CAPTURED__",
                           json.dumps(data, separators=(",", ":")))

    swaps = [
        ('<link rel="stylesheet" href="app.css">', "<style>\n" + css + "\n</style>"),
        ('<script src="app.js"></script>',
         "<script>\n" + mock + "\n</script>\n<script>\n" + js + "\n</script>"),
        ("<title>Opus</title>",
         "<title>Opus · Licensing desk</title>\n"
         '<meta name="description" content="The Opus interface, running on '
         'captured data: a day of sheet music orders matched, scored, stamped '
         'and logged.">'),
        ('<main class="main" id="main">', '<main class="main" id="main">' + BANNER),
    ]
    for needle, replacement in swaps:
        if needle not in html:
            sys.exit("app.html no longer contains {!r}. The demo build needs "
                     "updating alongside the interface.".format(needle[:40]))
        html = html.replace(needle, replacement, 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")

    print("wrote {}  ({:.0f} KB)".format(
        OUT.relative_to(ROOT), len(html.encode("utf-8")) / 1024))
    print("  {} orders, {} ledger rows, {} connectors, {} pieces".format(
        len(data["orders"]["orders"]), len(data["ledger"]["ledger"]),
        len(data["connectors"]["connectors"]), len(data["catalog"]["catalog"])))
    print("  generated from webui/static — the app and the demo are one UI")
    return 0


if __name__ == "__main__":
    sys.exit(build())
