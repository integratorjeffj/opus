#!/usr/bin/env python3
"""
opus.py - licensing stamper for small sheet music publishers

An opus number is how a composer's work gets catalogued: an identifier,
assigned once, that says precisely which piece you mean. This does the same for
copies -- every file issued carries a name on the page and a row in the ledger,
and stays identifiable for as long as it exists.

Stamps sheet music PDFs with a per-licensee notice and purchase date, locks
them against editing, and records every copy issued in a permanent ledger.
Built to process a whole PayPal order, or a whole day of orders, at once.

DEMO DATA ONLY
    This is a portfolio build. Load fictional, redacted or otherwise
    non-sensitive files -- never a live PayPal export, and no music you do not
    hold the rights to. The app window asks you to confirm before anything can
    be queued; the command line prompts before it stamps. Pass --demo-ack (or
    set OPUS_DEMO_ACK=1) to confirm up front in a scripted run. Safe sample
    data ships in examples/ and samples/.

Double-click this file (or run it with no arguments) to open the app window.
No terminal knowledge required.

TWO WAYS TO USE IT
    Manual Batch tab  - type a licensee name, queue files or a folder, stamp.
    PayPal Orders tab - drop in a PayPal activity CSV and it reads the buyer
                        names, order dates and item titles, maps each title to
                        its PDFs using a catalog map, skips anything already
                        issued, and does the whole lot.

WHAT IT DOES PER FILE
    1. Draws a notice on every page:
         "This music is licensed for use by <LICENSEE>. Distribution and/or
          use otherwise is prohibited by federal law."
       and a footer:
         "Licensed Purchase <m/d/yy>"
       The text is merged into the page content, not added as a note, so it
       cannot be selected and deleted as a separate object.
    2. Flattens any form fields or markup (via qpdf, when available).
    3. Encrypts with AES-256: opens with no password, but editing, copying,
       commenting and page re-assembly are blocked. Printing stays allowed.
    4. Appends a row to license_ledger.csv recording who received what, when,
       under which order reference, and with which owner password.

COMMAND LINE (optional -- the app window is the normal way to use this)
    python3 opus.py --licensee "First Baptist Church" \
        --out ./licensed --order PP-1042 --folder ./catalog/anthem
    python3 opus.py --paypal ~/Downloads/activity.csv \
        --catalog catalog_map.csv --out ./licensed
    python3 opus.py --make-catalog ./catalog -o catalog_map.csv
    python3 opus.py --paypal examples/paypal_sample.csv         --catalog examples/catalog_map.csv --out ./licensed --dry-run

REQUIREMENTS
    pip3 install pypdf reportlab pikepdf cryptography
    qpdf is optional (brew install qpdf); without it the extra form-flatten
    pass is skipped, which is fine for engraved music.

MIT licensed. See LICENSE.
"""

import argparse
import csv
import difflib
import io
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import traceback
from datetime import date, datetime
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import DependencyError
except ImportError:
    sys.exit("Missing dependency 'pypdf'. Install it with: pip3 install pypdf")

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
except ImportError:
    sys.exit("Missing dependency 'reportlab'. Install it with: pip3 install reportlab")

try:
    import pikepdf
except ImportError:
    sys.exit("Missing dependency 'pikepdf'. Install it with: pip3 install pikepdf")

# pypdf needs this to read an AES-encrypted source, which happens whenever an
# already-licensed file is re-stamped. Imported here rather than lazily so the
# failure is a clear message at startup instead of a confusing one mid-batch.
try:
    import cryptography  # noqa: F401
except ImportError:
    sys.exit("Missing dependency 'cryptography'. Install it with: "
             "pip3 install cryptography")


# ---------------------------------------------------------------------------
# Configuration -- edit these if the wording or look ever changes
# ---------------------------------------------------------------------------

HEADER_TEMPLATE = (
    "This music is licensed for use by {licensee}. Distribution and/or use "
    "otherwise is prohibited by federal law."
)
FOOTER_TEMPLATE = "Licensed Purchase {date}"

FONT_NAME = "Helvetica"
FONT_SIZE = 8
MARGIN = 18          # points of clearance from the page edge
LINE_GAP = 2         # points between wrapped header lines
LEDGER_NAME = "license_ledger.csv"

LEDGER_FIELDS = [
    "stamped_at",
    "licensee",
    "order_ref",
    "item_title",
    "license_date",
    "source_file",
    "output_file",
    "pages",
    "owner_password",
    "status",
    "notes",
]

# PayPal rows we never treat as a sale.
PAYPAL_TYPE_DENYLIST = (
    "refund", "reversal", "chargeback", "withdrawal", "transfer", "fee",
    "authorization", "hold", "release", "dispute", "cancel", "payout",
    "currency conversion", "subscription cancel",
)

# Fuzzy-match confidence below which a title is reported as unmatched.
TITLE_MATCH_CUTOFF = 0.86


# ---------------------------------------------------------------------------
# Release identity, bundled resources and update checks
#
# When PyInstaller freezes this file into an app, the source tree is gone and
# the bundled data lives in a temporary extraction directory. resource_path()
# is the single place that difference is handled, so the rest of the module can
# keep referring to "examples/paypal_sample.csv" and be right either way.
# ---------------------------------------------------------------------------

__version__ = "1.0.0"

APP_NAME = "Opus"
RELEASES_API = "https://api.github.com/repos/integratorjeffj/opus/releases/latest"
RELEASES_PAGE = "https://github.com/integratorjeffj/opus/releases/latest"
NO_UPDATE_ENV = "OPUS_NO_UPDATE_CHECK"


def is_frozen():
    """True when running from a PyInstaller-built app rather than source."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(relative):
    """Resolve a path to data shipped alongside the code.

    Frozen: PyInstaller's extraction dir. Source: the folder holding this file.
    """
    if is_frozen():
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / relative


def bundled_demo_paths():
    """The sample PayPal export and catalog map shipped inside the app.

    Returns (paypal_csv, catalog_csv), or (None, None) if they are not present
    -- a source checkout with the folders deleted, for instance.
    """
    paypal = resource_path(Path("examples") / "paypal_sample.csv")
    catalog = resource_path(Path("examples") / "catalog_map.csv")
    if paypal.is_file() and catalog.is_file():
        return paypal, catalog
    return None, None


def _parse_version(text):
    """'v1.2.3' or '1.2.3' -> (1, 2, 3). Trailing junk is ignored."""
    nums = re.findall(r"\d+", (text or "").strip())
    return tuple(int(n) for n in nums[:3]) or (0,)


def check_for_update(timeout=4.0):
    """Ask GitHub whether a newer release exists.

    Returns a version string when one is available, None otherwise or on any
    failure. Deliberately quiet: a version check is never worth an error
    dialog, and it must never be the reason the app fails to start.
    """
    if os.environ.get(NO_UPDATE_ENV, "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        import json
        from urllib.request import Request, urlopen
        req = Request(RELEASES_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "{}/{}".format(APP_NAME, __version__),
        })
        with urlopen(req, timeout=timeout) as resp:
            tag = json.load(resp).get("tag_name", "")
        if tag and _parse_version(tag) > _parse_version(__version__):
            return tag.lstrip("vV")
    except Exception:
        pass
    return None


def check_for_update_async(callback):
    """Run check_for_update off the main thread and hand the result back.

    The callback is invoked with the version string, or not at all. Used by the
    app window so a slow network never delays the UI appearing.
    """
    def worker():
        found = check_for_update()
        if found:
            try:
                callback(found)
            except Exception:
                pass
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Demo-data acknowledgement
#
# Opus is published as a portfolio demo. Anyone trying it out is handling a
# payment export and a folder of PDFs, which in the real world means buyer
# names, email addresses and copyrighted engravings. The gate below makes the
# operator say so explicitly, before a single file is chosen.
# ---------------------------------------------------------------------------

ACK_TITLE = "Use demo data only"

ACK_BODY = (
    "Opus is a demonstration build.\n\n"
    "A PayPal activity export contains real buyer names, email addresses and "
    "transaction IDs. A catalog folder contains copyrighted engravings. "
    "Neither belongs in a demo.\n\n"
    "Before you continue, confirm that the files you are about to load are "
    "fictional, redacted, or otherwise non-sensitive, and that you have the "
    "right to use any music you point Opus at.\n\n"
    "Sample data that is safe to use ships in examples/ and samples/."
)

ACK_CHECKBOX = ("I confirm I will only load fictional or non-sensitive "
                "material, and only music I have the right to use.")

ACK_ENV = "OPUS_DEMO_ACK"


def _ack_preapproved(force=False):
    if force:
        return True
    return os.environ.get(ACK_ENV, "").strip().lower() in ("1", "true", "yes")


def demo_ack_cli(force=False):
    """Confirm demo-data handling before anything is written.

    Returns True to proceed. Honours --demo-ack and the OPUS_DEMO_ACK
    environment variable so scripted runs are not blocked, and refuses rather
    than assumes when there is no terminal to prompt at.
    """
    if _ack_preapproved(force):
        return True

    rule = "-" * 72
    print("\n" + rule)
    print(ACK_TITLE.upper())
    print(rule)
    for para in ACK_BODY.split("\n\n"):
        print(textwrap.fill(para, 72))
        print()
    print(rule)

    if not sys.stdin.isatty():
        print("No terminal available to confirm at. Re-run with --demo-ack, or\n"
              "set {}=1, once the above is true of your input.".format(ACK_ENV))
        return False

    try:
        reply = input("Type 'yes' to confirm the above, anything else to stop: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if reply.strip().lower() in ("y", "yes"):
        print()
        return True
    print("Stopped. Nothing was written.\n")
    return False


def demo_ack_dialog(tk, ttk, parent):
    """Modal acknowledgement shown before the main window becomes usable.

    Returns True only if the operator ticked the box and pressed Continue.
    """
    if _ack_preapproved():
        return True

    dlg = tk.Toplevel(parent)
    dlg.title(ACK_TITLE)
    dlg.resizable(False, False)
    dlg.transient(parent)

    frame = ttk.Frame(dlg, padding=18)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=ACK_TITLE,
              font=("Helvetica", 14, "bold")).pack(anchor="w")
    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(8, 10))
    ttk.Label(frame, text=ACK_BODY, wraplength=470,
              justify="left").pack(anchor="w")
    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(14, 10))

    agreed = tk.BooleanVar(value=False)
    result = {"ok": False}

    check = ttk.Checkbutton(frame, text=ACK_CHECKBOX, variable=agreed,
                            onvalue=True, offvalue=False)
    check.pack(anchor="w")

    row = ttk.Frame(frame)
    row.pack(fill="x", pady=(14, 0))

    def do_quit():
        result["ok"] = False
        dlg.destroy()

    def do_continue():
        if agreed.get():
            result["ok"] = True
            dlg.destroy()

    ttk.Button(row, text="Quit", command=do_quit).pack(side="right", padx=(8, 0))
    go_btn = ttk.Button(row, text="Continue", command=do_continue,
                        state="disabled")
    go_btn.pack(side="right")

    def sync(*_):
        go_btn.configure(state=("normal" if agreed.get() else "disabled"))

    agreed.trace_add("write", sync)

    dlg.protocol("WM_DELETE_WINDOW", do_quit)
    dlg.bind("<Escape>", lambda _e: do_quit())

    dlg.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    dlg.geometry("+{}+{}".format(px + max((pw - w) // 2, 0),
                                 py + max((ph - h) // 3, 0)))

    dlg.grab_set()
    check.focus_set()
    parent.wait_window(dlg)
    return result["ok"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def format_date(d):
    """m/d/yy with no leading zeros, without relying on platform-specific
    strftime flags such as %-m."""
    return "{}/{}/{}".format(d.month, d.day, d.strftime("%y"))


def parse_date_arg(value):
    """Accept the date formats PayPal and humans actually produce."""
    value = (value or "").strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y",
                "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("Could not read date '{}'. Use M/D/YY.".format(value))


def slugify(text, max_len=40):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return (slug[:max_len].rstrip("_") or "licensee")


def unique_path(path):
    """Never overwrite an existing output. Append _2, _3, ... if needed."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / "{}_{}{}".format(stem, n, suffix)
        if not candidate.exists():
            return candidate
        n += 1


def qpdf_available():
    return shutil.which("qpdf") is not None


def normalize_title(text):
    """Lowercase, drop punctuation and filler so 'Anthem for Brass (Score)'
    and 'anthem for brass - score' land in the same place."""
    t = (text or "").lower()
    t = re.sub(r"[\u2018\u2019\u201c\u201d]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def read_csv_rows(path):
    """Read a CSV tolerantly -- PayPal exports are usually UTF-8 with a BOM,
    but older ones come out as Latin-1."""
    path = Path(path)
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with path.open("r", newline="", encoding=encoding) as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    raise RuntimeError("Could not read {} in any common encoding.".format(path))


# ---------------------------------------------------------------------------
# Stamping core
# ---------------------------------------------------------------------------

def make_overlay_page(page_width, page_height, header_text, footer_text):
    """Build a page-sized overlay containing only the header and footer."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    c.setFont(FONT_NAME, FONT_SIZE)

    max_width = page_width - 2 * MARGIN
    lines = simpleSplit(header_text, FONT_NAME, FONT_SIZE, max_width)

    # Start one full line height below the top edge so ascenders are not
    # clipped off the top of the page.
    y = page_height - MARGIN - FONT_SIZE
    for line in lines:
        c.drawCentredString(page_width / 2.0, y, line)
        y -= FONT_SIZE + LINE_GAP

    c.drawCentredString(page_width / 2.0, MARGIN - FONT_SIZE + 2, footer_text)

    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def stamp_pages(input_path, header_text, footer_text):
    """Merge the overlay into every page. Returns (pdf_bytes, page_count).

    Rotated pages are handled by rotating the overlay to match, so the stamp
    always reads the same way up as the music does.
    """
    reader = PdfReader(str(input_path))
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except DependencyError as exc:
            # Never blame the file for a missing library.
            raise RuntimeError(
                "Source PDF is encrypted and this build cannot read it: "
                "{}".format(exc))
        except Exception as exc:
            raise RuntimeError(
                "Source PDF could not be opened: {}".format(exc))
        if not opened:
            raise RuntimeError(
                "Source PDF is password-protected and cannot be opened.")

    writer = PdfWriter()

    for page in reader.pages:
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        if rotation in (90, 270):
            overlay = make_overlay_page(height, width, header_text, footer_text)
            overlay.rotate(360 - rotation)
            overlay.transfer_rotation_to_content()
        else:
            overlay = make_overlay_page(width, height, header_text, footer_text)
            if rotation == 180:
                overlay.rotate(180)
                overlay.transfer_rotation_to_content()

        page.merge_page(overlay)
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), len(reader.pages)


def flatten_annotations(pdf_bytes):
    """Flatten leftover form fields or markup into static page content.
    Returns (pdf_bytes, note)."""
    if not qpdf_available():
        return pdf_bytes, "qpdf not installed; flatten step skipped"

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        dst = Path(tmp) / "out.pdf"
        src.write_bytes(pdf_bytes)
        result = subprocess.run(
            ["qpdf", "--flatten-annotations=all", str(src), str(dst)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not dst.exists():
            return pdf_bytes, "flatten warning: {}".format(
                (result.stderr or "").strip()[:120])
        return dst.read_bytes(), ""


def protect(pdf_bytes, owner_password):
    """AES-256 encrypt: no password to open or print, editing blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        dst = Path(tmp) / "out.pdf"
        src.write_bytes(pdf_bytes)

        with pikepdf.open(src) as pdf:
            permissions = pikepdf.Permissions(
                accessibility=True,
                extract=False,
                modify_annotation=False,
                modify_assembly=False,
                modify_form=False,
                modify_other=False,
                print_lowres=True,
                print_highres=True,
            )
            pdf.save(dst, encryption=pikepdf.Encryption(
                user="", owner=owner_password, allow=permissions, R=6))
        return dst.read_bytes()


def stamp_one(source, licensee, out_dir, license_date, owner_password,
              order_ref="", item_title=""):
    """Stamp a single PDF. Returns a ledger record dict (never raises)."""
    source = Path(source)
    record = {
        "stamped_at": datetime.now().isoformat(timespec="seconds"),
        "licensee": licensee,
        "order_ref": order_ref,
        "item_title": item_title,
        "license_date": format_date(license_date),
        "source_file": str(source),
        "output_file": "",
        "pages": "",
        "owner_password": owner_password,
        "status": "ok",
        "notes": "",
    }

    try:
        header_text = HEADER_TEMPLATE.format(licensee=licensee)
        footer_text = FOOTER_TEMPLATE.format(date=format_date(license_date))

        stamped, page_count = stamp_pages(source, header_text, footer_text)
        flattened, note = flatten_annotations(stamped)
        protected = protect(flattened, owner_password)

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Nearly every product folder contains a "score.pdf", so the item
        # title goes into the name too. Without it, a buyer who orders two
        # pieces gets score__Name.pdf and score__Name_2.pdf and no way to
        # tell which is which.
        stem = source.stem
        if item_title and normalize_title(item_title) != normalize_title(stem):
            stem = "{}_{}".format(slugify(item_title, 32), stem)
        target = unique_path(
            out_dir / "{}__{}.pdf".format(stem, slugify(licensee)))
        target.write_bytes(protected)

        record["output_file"] = str(target)
        record["pages"] = page_count
        record["notes"] = note
    except Exception as exc:
        record["status"] = "FAILED"
        record["notes"] = "{}: {}".format(type(exc).__name__, exc)[:300]

    return record


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def ledger_path(out_dir):
    return Path(out_dir) / LEDGER_NAME


def append_ledger(out_dir, records):
    """Append records, creating the file with headers if new."""
    path = ledger_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LEDGER_FIELDS,
                                extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for rec in records:
            writer.writerow(rec)
    return path


def already_stamped_refs(out_dir):
    """Order references already recorded as successfully stamped, so a repeat
    import of an overlapping PayPal export does not re-issue the same files."""
    path = ledger_path(out_dir)
    if not path.exists():
        return set()
    try:
        rows = read_csv_rows(path)
    except Exception:
        return set()
    return {
        (r.get("order_ref") or "").strip()
        for r in rows
        if (r.get("status") or "").strip() == "ok" and (r.get("order_ref") or "").strip()
    }


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def run_batch(sources, licensee, out_dir, license_date=None, order_ref="",
              item_title="", owner_password=None, one_password_per_batch=True,
              progress=None, write_ledger=True):
    """Stamp many PDFs for one licensee. Returns (records, ledger_path)."""
    license_date = license_date or date.today()
    batch_password = owner_password or secrets.token_urlsafe(12)

    records = []
    total = len(sources)
    for i, src in enumerate(sources, start=1):
        if progress:
            progress(i, total, Path(src).name)
        pw = batch_password if one_password_per_batch else secrets.token_urlsafe(12)
        records.append(stamp_one(src, licensee, out_dir, license_date, pw,
                                 order_ref, item_title))

    path = append_ledger(out_dir, records) if write_ledger else ledger_path(out_dir)
    return records, path


def collect_pdfs(folder, recursive=True):
    """All PDFs in a folder, sorted, skipping anything already stamped."""
    folder = Path(folder)
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return [p for p in sorted(folder.glob(pattern))
            if p.is_file() and "__" not in p.stem]


# ---------------------------------------------------------------------------
# Catalog map: item title -> the PDFs that make up that product
# ---------------------------------------------------------------------------

CATALOG_FIELDS = ["item_title", "path", "notes"]


def make_catalog_template(catalog_root, out_csv):
    """Scan a catalog folder and write a starter map, one row per subfolder.
    She then edits the item_title column to match her PayPal item titles."""
    catalog_root = Path(catalog_root)
    rows = []
    for sub in sorted(p for p in catalog_root.iterdir() if p.is_dir()):
        pdfs = collect_pdfs(sub)
        if pdfs:
            rows.append({
                "item_title": sub.name.replace("_", " ").replace("-", " ").title(),
                "path": str(sub),
                "notes": "{} PDF(s)".format(len(pdfs)),
            })
    loose = [p for p in sorted(catalog_root.glob("*.pdf")) if "__" not in p.stem]
    for p in loose:
        rows.append({"item_title": p.stem.replace("_", " ").title(),
                     "path": str(p), "notes": "single file"})

    out_csv = Path(out_csv)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return out_csv, len(rows)


def load_catalog(map_csv):
    """Return {normalized_title: (display_title, [pdf paths])}.

    A row's path may be a folder (every PDF inside becomes part of the
    product) or a single PDF. Relative paths resolve against the map file's
    own folder, so the whole thing stays portable.
    """
    map_csv = Path(map_csv)
    base = map_csv.parent
    catalog = {}
    for row in read_csv_rows(map_csv):
        title = (row.get("item_title") or "").strip()
        raw = (row.get("path") or "").strip()
        if not title or not raw:
            continue
        target = Path(raw)
        if not target.is_absolute():
            target = (base / target).resolve()
        if target.is_dir():
            files = collect_pdfs(target)
        elif target.is_file():
            files = [target]
        else:
            files = []
        catalog[normalize_title(title)] = (title, files)
    return catalog


def match_title(item_title, catalog):
    """Find the catalog entry for a PayPal item title.
    Returns (display_title, files, how) -- how is 'exact', 'contains',
    'fuzzy', or 'none'."""
    key = normalize_title(item_title)
    if not key:
        return ("", [], "none")
    if key in catalog:
        title, files = catalog[key]
        return (title, files, "exact")

    # A PayPal title often carries extra words: "Anthem for Brass - PDF download"
    contains = [k for k in catalog if k and (k in key or key in k)]
    if len(contains) == 1:
        title, files = catalog[contains[0]]
        return (title, files, "contains")
    if len(contains) > 1:
        best = max(contains, key=len)
        title, files = catalog[best]
        return (title, files, "contains")

    close = difflib.get_close_matches(key, list(catalog), n=1,
                                      cutoff=TITLE_MATCH_CUTOFF)
    if close:
        title, files = catalog[close[0]]
        return (title, files, "fuzzy")
    return ("", [], "none")


# ---------------------------------------------------------------------------
# PayPal CSV
# ---------------------------------------------------------------------------

PAYPAL_FIELD_CANDIDATES = {
    "name": ["name", "buyer name", "from name", "payer name"],
    "item": ["item title", "item name", "description", "subject", "note"],
    "txn": ["transaction id", "transaction  id", "receipt id"],
    "date": ["date"],
    "status": ["status"],
    "type": ["type"],
    "email": ["from email address", "email", "payer email", "buyer email"],
    "qty": ["quantity", "qty"],
    "invoice": ["invoice number", "invoice id"],
}


def _norm_header(h):
    return re.sub(r"[^a-z0-9]+", " ", (h or "").lower()).strip()


def map_paypal_columns(fieldnames):
    """PayPal changes its export headers over the years. Match by meaning."""
    lookup = {_norm_header(f): f for f in (fieldnames or [])}
    mapping = {}
    for key, candidates in PAYPAL_FIELD_CANDIDATES.items():
        for cand in candidates:
            if cand in lookup:
                mapping[key] = lookup[cand]
                break
    return mapping


def read_paypal_orders(csv_path):
    """Parse a PayPal activity export into order dicts.

    Returns (orders, warnings). Refunds, transfers and fee rows are dropped;
    only completed payments survive.
    """
    rows = read_csv_rows(csv_path)
    warnings = []
    if not rows:
        return [], ["The PayPal CSV has no rows."]

    mapping = map_paypal_columns(rows[0].keys())
    for required in ("name", "item"):
        if required not in mapping:
            warnings.append(
                "Could not find a '{}' column in this export. Columns seen: {}"
                .format(required, ", ".join(list(rows[0].keys())[:12]))
            )
    if "name" not in mapping or "item" not in mapping:
        return [], warnings

    orders = []
    for row in rows:
        status = (row.get(mapping.get("status", ""), "") or "").strip().lower()
        rtype = (row.get(mapping.get("type", ""), "") or "").strip().lower()

        if status and status not in ("completed", "complete"):
            continue
        if any(bad in rtype for bad in PAYPAL_TYPE_DENYLIST):
            continue

        buyer = (row.get(mapping["name"], "") or "").strip()
        item = (row.get(mapping["item"], "") or "").strip()
        if not buyer or not item:
            continue

        raw_date = (row.get(mapping.get("date", ""), "") or "").strip()
        try:
            order_date = parse_date_arg(raw_date) if raw_date else date.today()
        except ValueError:
            order_date = date.today()
            warnings.append("Unreadable date '{}' for {}; used today.".format(
                raw_date, buyer))

        txn = (row.get(mapping.get("txn", ""), "") or "").strip()
        invoice = (row.get(mapping.get("invoice", ""), "") or "").strip()

        orders.append({
            "buyer": buyer,
            "item_title": item,
            "order_ref": txn or invoice or "{}-{}".format(
                slugify(buyer, 16), order_date.isoformat()),
            "order_date": order_date,
            "email": (row.get(mapping.get("email", ""), "") or "").strip(),
        })

    if not orders:
        warnings.append(
            "No completed payment rows found. Check that the export covers the "
            "right date range and includes item titles."
        )
    return orders, warnings


def plan_paypal_batch(orders, catalog, out_dir):
    """Attach matched files and a disposition to each order, without stamping.
    Lets her review before anything is written."""
    done = already_stamped_refs(out_dir) if out_dir else set()
    plan = []
    for order in orders:
        title, files, how = match_title(order["item_title"], catalog)
        entry = dict(order)
        entry["matched_title"] = title
        entry["files"] = files
        entry["match"] = how

        if order["order_ref"] in done:
            entry["disposition"] = "already stamped"
        elif how == "none":
            entry["disposition"] = "no catalog match"
        elif not files:
            entry["disposition"] = "matched, no PDFs found"
        else:
            entry["disposition"] = "ready"
        plan.append(entry)
    return plan


def run_paypal_plan(plan, out_dir, progress=None):
    """Stamp every 'ready' entry. Returns (records, ledger_path, summary)."""
    ready = [e for e in plan if e["disposition"] == "ready"]
    total_files = sum(len(e["files"]) for e in ready)
    all_records = []
    done_files = 0

    for entry in ready:
        pw = secrets.token_urlsafe(12)
        for src in entry["files"]:
            done_files += 1
            if progress:
                progress(done_files, total_files,
                         "{} - {}".format(entry["buyer"], Path(src).name))
            all_records.append(stamp_one(
                src, entry["buyer"], out_dir, entry["order_date"], pw,
                entry["order_ref"], entry["matched_title"] or entry["item_title"]))

    path = append_ledger(out_dir, all_records) if all_records else ledger_path(out_dir)
    summary = {
        "orders": len(ready),
        "files_ok": len([r for r in all_records if r["status"] == "ok"]),
        "files_failed": len([r for r in all_records if r["status"] != "ok"]),
        "skipped": len(plan) - len(ready),
    }
    return all_records, path, summary


# ---------------------------------------------------------------------------
# App window
# ---------------------------------------------------------------------------

def launch_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("The app window needs Python's tkinter.\n"
              "Install Python from python.org (it includes tkinter), or use\n"
              "the command line -- run this file with --help.")
        return 1

    state = {"files": [], "out_dir": "", "plan": [], "catalog": {},
             "catalog_path": "", "paypal_path": ""}

    root = tk.Tk()
    root.title("{} {} - Sheet Music Licensing".format(APP_NAME, __version__))
    root.geometry("880x680")
    root.minsize(780, 600)

    # Nothing can be queued until the operator acknowledges that the material
    # they are about to load is fictional or otherwise non-sensitive. The main
    # window stays hidden behind the dialog so there is no way around it.
    root.withdraw()
    if not demo_ack_dialog(tk, ttk, root):
        root.destroy()
        print("Demo-data acknowledgement declined. Nothing was loaded.")
        return 1
    root.deiconify()

    main = ttk.Frame(root, padding=14)
    main.pack(fill="both", expand=True)

    head = ttk.Frame(main)
    head.pack(fill="x")
    ttk.Label(head, text=APP_NAME,
              font=("Helvetica", 16, "bold")).pack(side="left")
    ttk.Label(head, text="v" + __version__,
              foreground="#777").pack(side="left", padx=(8, 0), pady=(5, 0))

    update_var = tk.StringVar(value="")
    update_lbl = ttk.Label(head, textvariable=update_var, foreground="#8a6320",
                           cursor="hand2")
    update_lbl.pack(side="right", pady=(5, 0))

    def open_releases(_event=None):
        if update_var.get():
            import webbrowser
            webbrowser.open(RELEASES_PAGE)

    update_lbl.bind("<Button-1>", open_releases)

    def announce_update(version):
        # Called from the update thread; hop back to the UI thread to touch tk.
        root.after(0, lambda: update_var.set(
            "Version {} is available - click to download".format(version)))

    check_for_update_async(announce_update)

    ttk.Label(main, text="Stamp, flatten and lock sheet music PDFs, then log "
                         "who received what.",
              foreground="#555").pack(anchor="w", pady=(0, 10))

    notebook = ttk.Notebook(main)
    notebook.pack(fill="both", expand=True)

    # ===================== Tab 1: Manual batch =============================
    tab1 = ttk.Frame(notebook, padding=10)
    notebook.add(tab1, text="  Manual Batch  ")

    form = ttk.LabelFrame(tab1, text="License details", padding=10)
    form.pack(fill="x")

    ttk.Label(form, text="Licensee name (exactly as it should print)").grid(
        row=0, column=0, sticky="w", columnspan=4)
    licensee_var = tk.StringVar()
    ttk.Entry(form, textvariable=licensee_var, width=70).grid(
        row=1, column=0, columnspan=4, sticky="we", pady=(2, 8))

    ttk.Label(form, text="Order / PayPal ref (optional)").grid(row=2, column=0, sticky="w")
    ttk.Label(form, text="Purchase date (M/D/YY)").grid(row=2, column=2, sticky="w", padx=(16, 0))
    order_var = tk.StringVar()
    ttk.Entry(form, textvariable=order_var, width=28).grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
    date_var = tk.StringVar(value=format_date(date.today()))
    ttk.Entry(form, textvariable=date_var, width=16).grid(
        row=3, column=2, sticky="w", padx=(16, 0), pady=(2, 0))
    form.columnconfigure(1, weight=1)

    files_frame = ttk.LabelFrame(tab1, text="PDFs to stamp", padding=10)
    files_frame.pack(fill="both", expand=True, pady=(10, 0))

    listbox = tk.Listbox(files_frame, height=8, activestyle="none")
    scroll = ttk.Scrollbar(files_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scroll.set)
    listbox.pack(side="left", fill="both", expand=True)
    scroll.pack(side="left", fill="y")

    count_var = tk.StringVar(value="0 file(s) queued")

    def refresh_list():
        listbox.delete(0, "end")
        for f in state["files"]:
            listbox.insert("end", str(f))
        count_var.set("{} file(s) queued".format(len(state["files"])))

    def add_files():
        for p in filedialog.askopenfilenames(title="Choose PDFs",
                                             filetypes=[("PDF files", "*.pdf")]):
            if Path(p) not in state["files"]:
                state["files"].append(Path(p))
        refresh_list()

    def add_folder():
        folder = filedialog.askdirectory(title="Choose a folder of PDFs")
        if folder:
            for p in collect_pdfs(folder):
                if p not in state["files"]:
                    state["files"].append(p)
            refresh_list()

    def clear_files():
        state["files"] = []
        refresh_list()

    btns = ttk.Frame(files_frame)
    btns.pack(side="left", fill="y", padx=(10, 0))
    ttk.Button(btns, text="Add files...", command=add_files).pack(fill="x", pady=2)
    ttk.Button(btns, text="Add folder...", command=add_folder).pack(fill="x", pady=2)
    ttk.Button(btns, text="Clear", command=clear_files).pack(fill="x", pady=2)

    ttk.Label(tab1, textvariable=count_var, foreground="#555").pack(anchor="w", pady=(6, 0))
    manual_btn = ttk.Button(tab1, text="Stamp All")
    manual_btn.pack(anchor="e", pady=(8, 0))

    # ===================== Tab 2: PayPal orders ============================
    tab2 = ttk.Frame(notebook, padding=10)
    notebook.add(tab2, text="  PayPal Orders  ")

    pick = ttk.LabelFrame(tab2, text="Inputs", padding=10)
    pick.pack(fill="x")

    paypal_var = tk.StringVar(value="(no PayPal CSV chosen)")
    catalog_var = tk.StringVar(value="(no catalog map chosen)")

    def pick_paypal():
        p = filedialog.askopenfilename(title="Choose the PayPal activity CSV",
                                       filetypes=[("CSV files", "*.csv")])
        if p:
            state["paypal_path"] = p
            paypal_var.set(p)

    def pick_catalog():
        p = filedialog.askopenfilename(title="Choose the catalog map CSV",
                                       filetypes=[("CSV files", "*.csv")])
        if p:
            state["catalog_path"] = p
            catalog_var.set(p)

    def build_catalog():
        folder = filedialog.askdirectory(title="Choose your catalog folder")
        if not folder:
            return
        dest = filedialog.asksaveasfilename(
            title="Save catalog map as", defaultextension=".csv",
            initialfile="catalog_map.csv", filetypes=[("CSV files", "*.csv")])
        if not dest:
            return
        path, n = make_catalog_template(folder, dest)
        state["catalog_path"] = str(path)
        catalog_var.set(str(path))
        messagebox.showinfo(
            "Catalog map created",
            "Wrote {} row(s) to:\n{}\n\nOpen it in Excel and edit the "
            "'item_title' column so each title matches what PayPal shows on "
            "the order.".format(n, path))

    ttk.Button(pick, text="PayPal CSV...", command=pick_paypal, width=18).grid(
        row=0, column=0, sticky="w", pady=3)
    ttk.Label(pick, textvariable=paypal_var, foreground="#555").grid(
        row=0, column=1, sticky="w", padx=10)
    ttk.Button(pick, text="Catalog map...", command=pick_catalog, width=18).grid(
        row=1, column=0, sticky="w", pady=3)
    ttk.Label(pick, textvariable=catalog_var, foreground="#555").grid(
        row=1, column=1, sticky="w", padx=10)
    ttk.Button(pick, text="Build catalog map from a folder...",
               command=build_catalog).grid(row=2, column=1, sticky="w",
                                           padx=10, pady=(4, 0))

    def load_sample():
        sample_paypal, sample_catalog = bundled_demo_paths()
        if not sample_paypal:
            messagebox.showinfo(
                "Sample data not found",
                "This build does not include the examples folder.")
            return
        state["paypal_path"] = str(sample_paypal)
        state["catalog_path"] = str(sample_catalog)
        paypal_var.set(str(sample_paypal))
        catalog_var.set(str(sample_catalog))
        messagebox.showinfo(
            "Sample order loaded",
            "Loaded the fictional PayPal export and its catalog map.\n\n"
            "It contains three good orders, one piece that is not in the "
            "catalog, plus a refund, a withdrawal and a pending payment that "
            "should all be filtered out.\n\nPress Review to see the plan.")

    if bundled_demo_paths()[0]:
        ttk.Button(pick, text="Try it with the sample order",
                   command=load_sample).grid(row=3, column=1, sticky="w",
                                             padx=10, pady=(6, 0))
    pick.columnconfigure(1, weight=1)

    review = ttk.LabelFrame(tab2, text="Review before stamping", padding=10)
    review.pack(fill="both", expand=True, pady=(10, 0))

    cols = ("date", "buyer", "item", "files", "disposition")
    tree = ttk.Treeview(review, columns=cols, show="headings",
                        selectmode="extended", height=11)
    for col, label, width in (("date", "Date", 80), ("buyer", "Buyer", 190),
                              ("item", "Item title", 240), ("files", "Files", 55),
                              ("disposition", "Status", 150)):
        tree.heading(col, text=label)
        tree.column(col, width=width, anchor="w")
    tscroll = ttk.Scrollbar(review, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tscroll.set)
    tree.pack(side="left", fill="both", expand=True)
    tscroll.pack(side="left", fill="y")

    tree.tag_configure("ready", foreground="#1d6b2f")
    tree.tag_configure("skip", foreground="#9c6a1a")

    def load_plan():
        if not state["paypal_path"]:
            messagebox.showwarning("PayPal CSV", "Choose the PayPal CSV first.")
            return
        if not state["catalog_path"]:
            messagebox.showwarning("Catalog map",
                                   "Choose or build a catalog map first.")
            return
        if not state["out_dir"]:
            messagebox.showwarning("Output folder", "Choose an output folder first.")
            return
        try:
            orders, warnings = read_paypal_orders(state["paypal_path"])
            catalog = load_catalog(state["catalog_path"])
        except Exception:
            messagebox.showerror("Could not read files", traceback.format_exc(limit=2))
            return

        state["catalog"] = catalog
        state["plan"] = plan_paypal_batch(orders, catalog, state["out_dir"])

        tree.delete(*tree.get_children())
        for i, e in enumerate(state["plan"]):
            tag = "ready" if e["disposition"] == "ready" else "skip"
            tree.insert("", "end", iid=str(i), tags=(tag,), values=(
                format_date(e["order_date"]), e["buyer"], e["item_title"],
                len(e["files"]), e["disposition"]))
        for i, e in enumerate(state["plan"]):
            if e["disposition"] == "ready":
                tree.selection_add(str(i))

        ready = sum(1 for e in state["plan"] if e["disposition"] == "ready")
        unmatched = sorted({e["item_title"] for e in state["plan"]
                            if e["disposition"] == "no catalog match"})
        note = "{} order(s) read, {} ready to stamp.".format(len(state["plan"]), ready)
        if unmatched:
            note += "\n\nNo catalog match for:\n  " + "\n  ".join(unmatched[:15])
            note += "\n\nAdd these titles to the catalog map and load again."
        if warnings:
            note += "\n\n" + "\n".join(warnings[:5])
        messagebox.showinfo("Loaded", note)

    pp_btns = ttk.Frame(tab2)
    pp_btns.pack(fill="x", pady=(8, 0))
    ttk.Button(pp_btns, text="Load orders", command=load_plan).pack(side="left")
    paypal_btn = ttk.Button(pp_btns, text="Stamp Selected Orders")
    paypal_btn.pack(side="right")
    ttk.Label(pp_btns, text="Highlighted rows will be stamped. "
                            "Cmd-click to change the selection.",
              foreground="#555").pack(side="left", padx=12)

    # ===================== Shared footer ===================================
    out_frame = ttk.Frame(main)
    out_frame.pack(fill="x", pady=(12, 0))
    out_var = tk.StringVar(value="(no folder chosen)")

    def pick_out():
        folder = filedialog.askdirectory(title="Where should licensed PDFs go?")
        if folder:
            state["out_dir"] = folder
            out_var.set(folder)

    ttk.Button(out_frame, text="Output folder...", command=pick_out).pack(side="left")
    ttk.Label(out_frame, textvariable=out_var, foreground="#555").pack(side="left", padx=10)

    status_var = tk.StringVar(value="Ready.")
    bar = ttk.Progressbar(main, mode="determinate")
    bar.pack(fill="x", pady=(10, 0))
    ttk.Label(main, textvariable=status_var).pack(anchor="w", pady=(4, 0))

    if not qpdf_available():
        ttk.Label(main, text="Note: qpdf not found. Stamping and locking still "
                             "work; the extra form-flatten pass is skipped.",
                  foreground="#8a6320").pack(anchor="w", pady=(6, 0))

    def make_progress(total):
        def progress(i, n, name):
            bar.configure(maximum=max(n, 1), value=i - 1)
            status_var.set("Stamping {} of {}: {}".format(i, n, name))
            root.update_idletasks()
        return progress

    def in_thread(fn):
        threading.Thread(target=fn, daemon=True).start()

    # --- manual run ---------------------------------------------------------
    def do_manual():
        licensee = licensee_var.get().strip()
        if not licensee:
            return messagebox.showwarning("Missing licensee", "Enter the licensee name.")
        if not state["files"]:
            return messagebox.showwarning("No files", "Add at least one PDF.")
        if not state["out_dir"]:
            return messagebox.showwarning("No output folder", "Choose an output folder.")
        try:
            lic_date = parse_date_arg(date_var.get())
        except ValueError as exc:
            return messagebox.showwarning("Date", str(exc))

        manual_btn.configure(state="disabled")

        def worker():
            try:
                records, ledger = run_batch(
                    state["files"], licensee, state["out_dir"],
                    license_date=lic_date, order_ref=order_var.get().strip(),
                    progress=make_progress(len(state["files"])))
            except Exception:
                messagebox.showerror("Error", traceback.format_exc(limit=2))
                manual_btn.configure(state="normal")
                return
            ok = [r for r in records if r["status"] == "ok"]
            bad = [r for r in records if r["status"] != "ok"]
            bar.configure(value=len(records))
            status_var.set("Done. {} stamped, {} failed.".format(len(ok), len(bad)))
            msg = ("{} file(s) stamped for:\n{}\n\nSaved to:\n{}\n\nLogged in:\n{}\n\n"
                   "Owner password for this batch:\n{}\n\nRecipients do NOT need "
                   "this password to open or print. It is already in the ledger."
                   ).format(len(ok), licensee, state["out_dir"], ledger,
                            ok[0]["owner_password"] if ok else "")
            if bad:
                msg += "\n\nFailed:\n" + "\n".join(
                    "  {} - {}".format(Path(r["source_file"]).name, r["notes"])
                    for r in bad)
            messagebox.showinfo("Batch complete", msg)
            manual_btn.configure(state="normal")

        in_thread(worker)

    manual_btn.configure(command=do_manual)

    # --- paypal run ---------------------------------------------------------
    def do_paypal():
        if not state["plan"]:
            return messagebox.showwarning("Nothing loaded", "Click 'Load orders' first.")
        chosen = {int(i) for i in tree.selection()}
        selected = [e for i, e in enumerate(state["plan"])
                    if i in chosen and e["disposition"] == "ready"]
        if not selected:
            return messagebox.showwarning(
                "Nothing to stamp",
                "No selected row is ready. Rows marked 'no catalog match' need "
                "a catalog entry; rows marked 'already stamped' were done "
                "in an earlier run.")

        total = sum(len(e["files"]) for e in selected)
        if not messagebox.askyesno(
                "Confirm", "Stamp {} file(s) across {} order(s)?".format(
                    total, len(selected))):
            return

        paypal_btn.configure(state="disabled")

        def worker():
            try:
                records, ledger, summary = run_paypal_plan(
                    selected, state["out_dir"], progress=make_progress(total))
            except Exception:
                messagebox.showerror("Error", traceback.format_exc(limit=2))
                paypal_btn.configure(state="normal")
                return
            bar.configure(value=total)
            status_var.set("Done. {} file(s) stamped, {} failed.".format(
                summary["files_ok"], summary["files_failed"]))
            msg = ("{} order(s) processed.\n{} file(s) stamped, {} failed.\n\n"
                   "Saved to:\n{}\n\nLogged in:\n{}\n\nEach order has its own "
                   "owner password, recorded in the ledger."
                   ).format(summary["orders"], summary["files_ok"],
                            summary["files_failed"], state["out_dir"], ledger)
            bad = [r for r in records if r["status"] != "ok"]
            if bad:
                msg += "\n\nFailed:\n" + "\n".join(
                    "  {} - {}".format(Path(r["source_file"]).name, r["notes"])
                    for r in bad[:10])
            messagebox.showinfo("Orders complete", msg)
            load_plan()
            paypal_btn.configure(state="normal")

        in_thread(worker)

    paypal_btn.configure(command=do_paypal)

    refresh_list()
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        return launch_gui()

    parser = argparse.ArgumentParser(
        description="Batch-stamp sheet music PDFs with a license header.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdfs", nargs="*", type=Path, help="Source PDFs")
    parser.add_argument("--folder", type=Path, help="Stamp every PDF in a folder")
    parser.add_argument("--licensee", help="Name to print in the header")
    parser.add_argument("--out", type=Path, help="Output folder")
    parser.add_argument("--order", default="", help="Order / PayPal reference")
    parser.add_argument("--date", dest="license_date", default=None,
                        help="Purchase date M/D/YY (default: today)")
    parser.add_argument("--owner-password", default=None,
                        help="Owner password (default: randomly generated)")
    parser.add_argument("--password-per-file", action="store_true",
                        help="Generate a separate owner password per file")
    parser.add_argument("--paypal", type=Path, help="PayPal activity CSV")
    parser.add_argument("--catalog", type=Path, help="Catalog map CSV")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --paypal: show the plan, stamp nothing")
    parser.add_argument("--make-catalog", type=Path,
                        help="Scan a catalog folder and write a starter map")
    parser.add_argument("-o", "--output-csv", type=Path, default=Path("catalog_map.csv"),
                        help="Where --make-catalog writes its map")
    parser.add_argument("--gui", action="store_true", help="Open the app window")
    parser.add_argument("--demo-ack", action="store_true",
                        help="Confirm up front that the input is fictional "
                             "or non-sensitive (skips the interactive "
                             "prompt; OPUS_DEMO_ACK=1 does the same)")
    parser.add_argument("--demo", action="store_true",
                        help="Use the fictional PayPal export and catalog "
                             "map bundled with the app")
    parser.add_argument("--version", action="version",
                        version="{} {}".format(APP_NAME, __version__))
    parser.add_argument("--no-update-check", action="store_true",
                        help="Skip the check for a newer release")
    args = parser.parse_args(argv)

    if args.no_update_check:
        os.environ[NO_UPDATE_ENV] = "1"

    if args.demo:
        demo_paypal, demo_catalog = bundled_demo_paths()
        if not demo_paypal:
            parser.error("--demo needs the bundled examples/ folder, which "
                         "is missing from this build.")
        args.paypal = args.paypal or demo_paypal
        args.catalog = args.catalog or demo_catalog
        if not args.out:
            parser.error("--demo still needs --out, so you choose where the "
                         "stamped files land.")

    if args.gui:
        return launch_gui()

    if args.make_catalog:
        path, n = make_catalog_template(args.make_catalog, args.output_csv)
        print("Wrote {} row(s) to {}".format(n, path))
        print("Edit the 'item_title' column so it matches your PayPal item titles.")
        return 0

    # --- PayPal mode --------------------------------------------------------
    if args.paypal:
        if not args.catalog or not args.out:
            parser.error("--paypal also needs --catalog and --out")
        orders, warnings = read_paypal_orders(args.paypal)
        for w in warnings:
            print("Note: {}".format(w))
        catalog = load_catalog(args.catalog)
        plan = plan_paypal_batch(orders, catalog, args.out)

        print("\n{:<10} {:<26} {:<30} {:>5}  {}".format(
            "DATE", "BUYER", "ITEM", "FILES", "STATUS"))
        for e in plan:
            print("{:<10} {:<26} {:<30} {:>5}  {}".format(
                format_date(e["order_date"]), e["buyer"][:26],
                e["item_title"][:30], len(e["files"]), e["disposition"]))

        unmatched = sorted({e["item_title"] for e in plan
                            if e["disposition"] == "no catalog match"})
        if unmatched:
            print("\nNo catalog match for:")
            for t in unmatched:
                print("  {}".format(t))

        if args.dry_run:
            print("\nDry run -- nothing stamped.")
            return 0

        if not demo_ack_cli(args.demo_ack):
            return 2

        records, ledger, summary = run_paypal_plan(
            plan, args.out,
            progress=lambda i, n, name: print("[{}/{}] {}".format(i, n, name)))
        print("\n{} order(s), {} file(s) stamped, {} failed, {} skipped.".format(
            summary["orders"], summary["files_ok"], summary["files_failed"],
            summary["skipped"]))
        print("Ledger: {}".format(ledger))
        return 0 if not summary["files_failed"] else 1

    # --- manual mode --------------------------------------------------------
    if not args.licensee or not args.out:
        parser.error("Manual mode needs --licensee and --out")

    sources = list(args.pdfs)
    if args.folder:
        sources.extend(collect_pdfs(args.folder))
    sources = [p for p in sources if p.exists()]
    if not sources:
        parser.error("No source PDFs found.")

    lic_date = parse_date_arg(args.license_date) if args.license_date else date.today()

    if not demo_ack_cli(args.demo_ack):
        return 2

    records, ledger = run_batch(
        sources, args.licensee, args.out, license_date=lic_date,
        order_ref=args.order, owner_password=args.owner_password,
        one_password_per_batch=not args.password_per_file,
        progress=lambda i, n, name: print("[{}/{}] {}".format(i, n, name)))

    ok = [r for r in records if r["status"] == "ok"]
    bad = [r for r in records if r["status"] != "ok"]
    print("\nStamped {} file(s), {} failed.".format(len(ok), len(bad)))
    for r in bad:
        print("  FAILED {} -> {}".format(r["source_file"], r["notes"]))
    print("Ledger: {}".format(ledger))
    if ok and not args.password_per_file:
        print("Owner password for this batch: {}".format(ok[0]["owner_password"]))
    print("(Recipients do not need this password to open or print.)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
