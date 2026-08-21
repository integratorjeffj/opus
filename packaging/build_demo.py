#!/usr/bin/env python3
"""Fill the browser demo with real data from this repository.

    python3 packaging/build_demo.py

docs/demo/index.html carries a __DATA__ placeholder. This script replaces it
with a JSON blob built from the actual sample ledger, the actual connector
registry, and real rendered pages from the committed PDFs -- so the demo cannot
quietly drift away from what Opus really does. Re-run it whenever the samples,
the connectors, or the version change.

The generated file is committed, because GitHub Pages serves static files and
there is no build step on the far end.
"""

import base64
import csv
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "docs" / "demo" / "index.html"
PLACEHOLDER = "__DATA__"

IMG_WIDTH = 640
IMG_QUALITY = 82

# The rung the demo is set to. 0.80 releases the three clean orders and
# still rejects the unmatched one, which is the interesting state to show.
DEMO_HOLD_BELOW = 0.80

MASTER = ROOT / "samples" / "catalog" / "Evening Bells" / "score.pdf"
ISSUED = (ROOT / "samples" / "licensed"
          / "Evening_Bells_score__First_Baptist_Church_Springfield.pdf")


def _encode(pil, width, quality=IMG_QUALITY):
    from PIL import Image
    w, h = pil.size
    if width and w != width:
        pil = pil.resize((width, max(1, int(h * width / w))), Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _first_page(pdf_path, password=None, scale=2.0):
    import pypdfium2 as pdfium
    kwargs = {"password": password} if password is not None else {}
    doc = pdfium.PdfDocument(str(pdf_path), **kwargs)
    return doc[0].render(scale=scale).to_pil().convert("RGB")


def render_page(pdf_path, password=None):
    """First page of a PDF as a base64 JPEG data URI."""
    return _encode(_first_page(pdf_path, password), IMG_WIDTH)


def render_notice(pdf_path, password=None):
    """A high-resolution crop of the stamped band across the top of the page.

    At the width the two page thumbnails are shown, an 8pt notice renders about
    four pixels tall and cannot be read -- which defeats the entire point of
    showing it. This crops just the notice and the top of the title so it is
    legible at the size a browser will display it.
    """
    pil = _first_page(pdf_path, password, scale=4.0)
    w, h = pil.size
    return _encode(pil.crop((0, 0, w, int(h * 0.085))), 1000, quality=86)


def read_ledger():
    path = ROOT / "samples" / "licensed" / "license_ledger.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [{
        "licensee": r["licensee"],
        "order_ref": r["order_ref"],
        "file": Path(r["output_file"].replace("\\", "/")).name,
        "source": Path(r["source_file"].replace("\\", "/")).name,
        "pages": r["pages"],
        "password": r["owner_password"],
    } for r in rows]


def read_connectors():
    sys.path.insert(0, str(ROOT))
    import connectors
    return [{"kind": k, "name": n, "label": lab, "state": st, "description": d}
            for k, n, lab, st, d in connectors.describe()]


def build_plan():
    """The review table, derived from the sample export rather than typed out.

    Carries the match provenance the planner already computes -- whether a
    title matched exactly, by containment, or fuzzily. Two different PayPal
    title strings resolving to one catalogue entry is the most interesting
    thing the matcher does, and it is invisible unless it is shown.
    """
    sys.path.insert(0, str(ROOT))
    import opus
    orders, _ = opus.read_paypal_orders(ROOT / "examples" / "paypal_sample.csv")
    catalog = opus.load_catalog(_temp_catalog_map())
    plan = opus.plan_paypal_batch(orders, catalog, None)

    # Score every order at the rung the demo is set to, so the review screen
    # shows what the machine concluded as well as what it matched.
    from connectors import confidence as conf
    assessed = dict((e.get("order_ref"), a)
                    for e, a in conf.assess_plan(catalog=catalog, plan=plan,
                                                 hold_below=DEMO_HOLD_BELOW))
    return [{
        "date": opus.format_date(e["order_date"]),
        "buyer": e["buyer"],
        "email": e["email"],
        "order_ref": e["order_ref"],
        "item": e["item_title"],
        "matched": e["matched_title"],
        "match": e["match"],
        "files": len(e["files"]),
        "parts": [Path(str(f)).name for f in e["files"]],
        "status": e["disposition"],
        "score": round(assessed[e["order_ref"]].score, 2),
        "verdict": assessed[e["order_ref"]].verdict,
        "signals": [s.as_dict() for s in assessed[e["order_ref"]].signals],
        "reasons": list(assessed[e["order_ref"]].reasons),
    } for e in plan]


def build_catalog():
    """The pieces and their parts, read from the committed sample catalogue."""
    sys.path.insert(0, str(ROOT))
    from connectors.catalog_local import collect_pdfs, scan_catalog
    out = []
    for item in scan_catalog(ROOT / "samples" / "catalog"):
        parts = [p.name for p in collect_pdfs(Path(item.ref))]
        pages = {}
        for name in parts:
            pages[name] = _page_count(Path(item.ref) / name)
        out.append({"title": item.title, "parts": parts, "pages": pages,
                    "files": item.file_count})
    return out


def _page_count(pdf_path):
    try:
        import pypdf
        return len(pypdf.PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


def build_dropped():
    """The export rows that never became orders, and why.

    The filtering is the quiet half of the product. Showing what was thrown
    away is more convincing than a count of what survived.
    """
    import csv as _csv
    rows = list(_csv.DictReader(
        (ROOT / "examples" / "paypal_sample.csv").open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        rtype = (r.get("Type") or "").strip()
        status = (r.get("Status") or "").strip()
        title = (r.get("Item Title") or "").strip()
        reason = None
        if "Refund" in rtype:
            reason = "Refund, not a sale"
        elif "Withdrawal" in rtype:
            reason = "Withdrawal, no item"
        elif status.lower() not in ("completed", "complete"):
            reason = "{}, not yet a completed sale".format(status)
        if reason:
            out.append({"who": (r.get("Name") or "").strip(),
                        "item": title or "(no item title)",
                        "amount": (r.get("Gross") or "").strip(),
                        "reason": reason})
    return out


def _temp_catalog_map():
    """A catalog map for the sample catalogue, written outside the repo tree."""
    import tempfile
    sys.path.insert(0, str(ROOT))
    from connectors.catalog_local import write_catalog_map
    dest = Path(tempfile.mkdtemp()) / "catalog_map.csv"
    return write_catalog_map(ROOT / "samples" / "catalog", dest)


def build_progress(ledger):
    """The progress lines the app actually prints: buyer and source filename."""
    total = len(ledger)
    return ["[{}/{}] {} - {}".format(i, total, r["licensee"], r["source"])
            for i, r in enumerate(ledger, 1)]


def read_version():
    src = (ROOT / "opus.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    return m.group(1) if m else "0.0.0"


def main():
    if not DEMO.is_file():
        sys.exit("Missing {}".format(DEMO))

    ledger = read_ledger()
    data = {
        "version": read_version(),
        "hold_below": DEMO_HOLD_BELOW,
        "paths": {
            "csv": "examples/paypal_sample.csv",
            "catalog": "examples/catalog_map.csv",
        },
        "plan": build_plan(),
        "catalog": build_catalog(),
        "dropped": build_dropped(),
        "ledger": ledger,
        "progress": build_progress(ledger),
        "connectors": read_connectors(),
        "manual": ["Evening Bells/choral_score.pdf",
                   "Evening Bells/organ.pdf",
                   "Evening Bells/score.pdf"],
        "images": {
            "before": render_page(MASTER),
            "after": render_page(ISSUED, password=""),
            "notice": render_notice(ISSUED, password=""),
        },
    }

    html = DEMO.read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"))

    if PLACEHOLDER in html:
        html = html.replace(PLACEHOLDER, blob, 1)
    else:
        # Re-run over an already-generated file: swap the previous blob out.
        new, n = re.subn(r"(var DATA = )\{.*?\};(\s*\n)",
                         lambda m: m.group(1) + blob + ";" + m.group(2),
                         html, count=1, flags=re.S)
        if not n:
            sys.exit("Could not find __DATA__ or a previous DATA blob in "
                     "docs/demo/index.html.")
        html = new

    DEMO.write_text(html, encoding="utf-8")

    size = len(html.encode("utf-8"))
    print("wrote {}  ({:.0f} KB)".format(DEMO.relative_to(ROOT), size / 1024))
    print("  {} plan rows, {} ledger rows, {} connectors, {} pieces, "
          "{} filtered rows".format(
              len(data["plan"]), len(ledger), len(data["connectors"]),
              len(data["catalog"]), len(data["dropped"])))
    ready = sum(1 for r in data["plan"] if r["status"] == "ready")
    print("  {} ready, {} file(s)".format(
        ready, sum(r["files"] for r in data["plan"] if r["status"] == "ready")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
