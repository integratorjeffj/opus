"""The engine's capabilities, as JSON.

Every endpoint here is a thin wrapper over something opus.py or a connector
already does. Nothing is computed twice and nothing is invented: if the UI
shows a number, this is where it came from, and it came from the engine.

The split is deliberate. `server.py` knows about HTTP and knows nothing about
licensing; this module knows about licensing and nothing about HTTP. That is
what lets the same UI run against a live API here and against a baked blob in
the published static demo.

CONVENTIONS
    Every handler returns (status, payload). Errors come back as
    {"error": "...", "detail": "..."} with a message written for the person
    reading it, not for a log.
"""

import sys
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import opus                                                    # noqa: E402
import connectors                                              # noqa: E402
from connectors import confidence as conf                      # noqa: E402
from connectors.catalog_local import scan_catalog, write_catalog_map  # noqa: E402

from . import state                                            # noqa: E402


class ApiError(Exception):
    """Something the caller should be told about, in words."""

    def __init__(self, message, status=400, detail=""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cache_dir():
    return state.config_dir() / "catalog-cache"


def _require(config, *keys):
    """Fetch a configured path, or explain exactly what is missing."""
    node = config
    for key in keys:
        node = (node or {}).get(key)
    if not node:
        raise ApiError(
            "Not set up yet: {} has no value.".format(" / ".join(keys)),
            status=409,
            detail="Open Settings and choose it, then try again.")
    return node


def _catalog_map(config):
    """A catalog map path, built from whatever catalogue source is configured."""
    explicit = (config.get("paths") or {}).get("catalog_map")
    if explicit and Path(explicit).is_file():
        return Path(explicit)

    root = (config.get("paths") or {}).get("catalog_root")
    if root and Path(root).is_dir():
        cache = _cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        return write_catalog_map(Path(root), cache / "catalog_map.csv")

    raise ApiError(
        "No catalogue configured.",
        status=409,
        detail="Choose a catalogue folder in Settings, or point at an "
               "existing catalog_map.csv.")


def _plan(config):
    """The current plan: orders matched against the catalogue, unstamped."""
    csv_path = _require(config, "paths", "paypal_csv")
    if not Path(csv_path).is_file():
        raise ApiError("That PayPal export is no longer there: {}".format(csv_path),
                       status=409)

    catalog = opus.load_catalog(_catalog_map(config))
    orders, warnings = opus.read_paypal_orders(Path(csv_path))
    out_dir = (config.get("paths") or {}).get("out_dir") or None
    plan = opus.plan_paypal_batch(orders, catalog, out_dir)
    return plan, catalog, warnings, out_dir


def _assess(plan, catalog, config, out_dir):
    hold_below = float((config.get("review") or {}).get("hold_below", 1.01))
    assessed = conf.assess_plan(
        plan, catalog,
        known_refs=opus.already_stamped_refs(out_dir) if out_dir else (),
        known_buyers=opus.ledger_buyers(out_dir) if out_dir else (),
        hold_below=hold_below)
    return assessed, hold_below


def _entry_json(entry, assessment=None):
    out = {
        "order_ref": entry.get("order_ref", ""),
        "buyer": entry.get("buyer", ""),
        "email": entry.get("email", ""),
        "date": opus.format_date(entry["order_date"]),
        "item": entry.get("item_title", ""),
        "matched": entry.get("matched_title", ""),
        "match": entry.get("match", "none"),
        "files": len(entry.get("files") or []),
        "parts": [Path(str(f)).name for f in (entry.get("files") or [])],
        "status": entry.get("disposition", ""),
    }
    if assessment is not None:
        out.update({
            "score": round(assessment.score, 2),
            "verdict": assessment.verdict,
            "signals": [s.as_dict() for s in assessment.signals],
            "reasons": list(assessment.reasons),
        })
    return out


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

def get_settings(config, _body=None):
    """Everything the UI needs to render Settings, secrets masked."""
    return 200, {
        "settings": state.redact(config),
        "config_path": str(state.config_path()),
        "version": opus.__version__,
    }


def put_settings(config, body):
    """Patch one section of the config. Returns the redacted result."""
    section = (body or {}).get("section")
    values = (body or {}).get("values")
    if section not in ("paths", "connectors", "review", "dashboard", "publisher"):
        raise ApiError("Unknown settings section: {!r}".format(section))
    if section == "publisher":
        config["publisher"] = str(values or "")
    else:
        state.apply_update(config, section, values)
    state.save(config)
    return 200, {"settings": state.redact(config), "saved": True}


def get_status(config, _body=None):
    """The one call the dashboard makes on load."""
    paths = config.get("paths") or {}
    catalog_ready = bool(paths.get("catalog_root") and
                         Path(paths["catalog_root"]).is_dir())
    export_ready = bool(paths.get("paypal_csv") and
                        Path(paths["paypal_csv"]).is_file())
    out_dir = paths.get("out_dir") or ""

    pieces = parts = 0
    if catalog_ready:
        try:
            items = scan_catalog(Path(paths["catalog_root"]))
            pieces = len(items)
            parts = sum(i.file_count for i in items)
        except Exception:
            catalog_ready = False

    issued = 0
    ledger_ok = None
    if out_dir and opus.ledger_path(out_dir).exists():
        try:
            rows = opus.read_csv_rows(opus.ledger_path(out_dir))
            issued = len([r for r in rows if (r.get("status") or "") == "ok"])
            ledger_ok = opus.verify_ledger(opus.ledger_path(out_dir))[0]
        except Exception:
            pass

    return 200, {
        "version": opus.__version__,
        "ready": catalog_ready and export_ready,
        "catalog": {"ready": catalog_ready, "pieces": pieces, "parts": parts,
                    "root": paths.get("catalog_root", "")},
        "export": {"ready": export_ready, "path": paths.get("paypal_csv", "")},
        "out_dir": out_dir,
        "issued": issued,
        "ledger_intact": ledger_ok,
        "hold_below": float((config.get("review") or {}).get("hold_below", 1.01)),
        "today": date.today().isoformat(),
    }


def get_orders(config, _body=None):
    """The plan, scored. This is the review queue."""
    plan, catalog, warnings, out_dir = _plan(config)
    assessed, hold_below = _assess(plan, catalog, config, out_dir)
    rows = [_entry_json(e, a) for e, a in assessed]
    counts = conf.summarize(assessed)
    return 200, {
        "orders": rows,
        "warnings": warnings,
        "hold_below": hold_below,
        "counts": {"release": counts.get("release", 0),
                   "hold": counts.get("hold", 0),
                   "reject": counts.get("reject", 0),
                   "total": len(rows)},
    }


def preview_threshold(config, body):
    """What would release at a different threshold, without changing anything.

    This is what makes the trust ladder usable: she can see the effect of
    lowering the dial before she commits to it.
    """
    try:
        candidate = float((body or {}).get("hold_below"))
    except (TypeError, ValueError):
        raise ApiError("hold_below must be a number.")

    plan, catalog, _warnings, out_dir = _plan(config)
    assessed, _ = _assess(plan, catalog, config, out_dir)

    out = []
    for entry, a in assessed:
        verdict = a.verdict
        if verdict != conf.REJECT:
            verdict = conf.RELEASE if a.score >= candidate else conf.HOLD
        out.append({"order_ref": entry.get("order_ref", ""),
                    "buyer": entry.get("buyer", ""),
                    "score": round(a.score, 2), "verdict": verdict})

    counts = {"release": 0, "hold": 0, "reject": 0}
    for row in out:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return 200, {"hold_below": candidate, "orders": out, "counts": counts}


def get_catalog(config, _body=None):
    root = _require(config, "paths", "catalog_root")
    if not Path(root).is_dir():
        raise ApiError("That catalogue folder is no longer there: {}".format(root),
                       status=409)
    from connectors.catalog_local import collect_pdfs
    pieces = []
    for item in scan_catalog(Path(root)):
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
    return 200, {"catalog": pieces, "root": str(root)}


def get_ledger(config, _body=None):
    out_dir = (config.get("paths") or {}).get("out_dir")
    if not out_dir or not opus.ledger_path(out_dir).exists():
        return 200, {"ledger": [], "intact": None, "report": ["Nothing issued yet."]}

    path = opus.ledger_path(out_dir)
    rows = opus.read_csv_rows(path)
    intact, report = opus.verify_ledger(path)
    return 200, {
        "ledger": [{
            "licensee": r.get("licensee", ""),
            "order_ref": r.get("order_ref", ""),
            "item_title": r.get("item_title", ""),
            "file": Path((r.get("output_file") or "").replace("\\", "/")).name,
            "source": Path((r.get("source_file") or "").replace("\\", "/")).name,
            "pages": r.get("pages", ""),
            "password": r.get("owner_password", ""),
            "status": r.get("status", ""),
            "confidence": r.get("confidence", ""),
            "decision": r.get("decision", ""),
            "delivered_at": r.get("delivered_at", ""),
            "delivery_channel": r.get("delivery_channel", ""),
            "delivery_ref": r.get("delivery_ref", ""),
            "stamped_at": r.get("stamped_at", ""),
            "notes": r.get("notes", ""),
        } for r in rows],
        "intact": intact,
        "report": report,
        "path": str(path),
    }


# Some connector settings are the same thing as a folder chosen in Settings.
# Without this, choosing a catalogue folder and then opening Connections shows
# "No catalog folder chosen", which is both wrong and alarming.
PATH_ALIASES = {
    ("local", "root"): "catalog_root",
    ("paypal-csv", "path"): "paypal_csv",
    ("portal", "root"): "portal_root",
}


def effective_settings(config, name):
    """A connector's settings, with anything already chosen in Settings filled in.

    Saved connector settings win; the shared paths are only a fallback, so
    someone who deliberately points a connector somewhere else keeps that.
    """
    values = dict((config.get("connectors") or {}).get(name) or {})
    paths = config.get("paths") or {}
    for (conn_name, key), path_key in PATH_ALIASES.items():
        if conn_name == name and not values.get(key) and paths.get(path_key):
            values[key] = paths[path_key]
    return values


def get_connectors(config, _body=None):
    """The gallery, with each connector's effective settings for the forms."""
    rows = []
    for kind, name, label, cstate, desc in connectors.describe():
        values = effective_settings(config, name)
        rows.append({"kind": kind, "name": name, "label": label,
                     "state": cstate, "description": desc,
                     "configured": bool(values),
                     "values": state.redact(values),
                     "fields": _fields_for(name)})
    return 200, {"connectors": rows}


def _fields_for(name):
    """What Settings should ask for, per connector. Drives the forms."""
    return {
        "local": [
            {"key": "root", "label": "Catalogue folder", "type": "folder"}],
        "gdrive": [
            {"key": "key_file", "label": "Service account JSON key", "type": "file"},
            {"key": "folder_id", "label": "Drive folder URL or id", "type": "text"}],
        "paypal-csv": [
            {"key": "path", "label": "PayPal activity export", "type": "file"}],
        "paypal-api": [
            {"key": "client_id", "label": "Client ID", "type": "text"},
            {"key": "client_secret", "label": "Secret", "type": "password"},
            {"key": "sandbox", "label": "Use sandbox", "type": "bool"}],
        "portal": [
            {"key": "root", "label": "Folder to publish into", "type": "folder"},
            {"key": "base_url", "label": "Public base URL", "type": "text"},
            {"key": "ttl_days", "label": "Link lifetime (days)", "type": "number"}],
        "smtp": [
            {"key": "host", "label": "Mail server", "type": "text"},
            {"key": "port", "label": "Port", "type": "number"},
            {"key": "username", "label": "Username", "type": "text"},
            {"key": "password", "label": "App password", "type": "password"},
            {"key": "sender", "label": "Send from", "type": "text"},
            {"key": "attach", "label": "Attach files instead of a link",
             "type": "bool"}],
    }.get(name, [])


def test_connector(config, body):
    """Press Test in the UI. Runs the adapter's own health check.

    This is the whole point of connector forms: she finds out it works here,
    not on the morning it fails to send.
    """
    kind = (body or {}).get("kind")
    name = (body or {}).get("name")
    if not kind or not name:
        raise ApiError("Which connector?")
    try:
        cls = connectors.get(kind, name)
    except connectors.ConnectorError as exc:
        raise ApiError(str(exc), status=404)

    settings = effective_settings(config, name)
    settings.update((body or {}).get("values") or {})
    # A masked secret means "keep what is stored".
    stored = (config.get("connectors") or {}).get(name) or {}
    for key, val in list(settings.items()):
        if state.is_secret(key) and val == state.MASK:
            settings[key] = stored.get(key, "")

    try:
        adapter = cls().configure(**settings)
        ok, message = adapter.health()
    except connectors.ConnectorError as exc:
        return 200, {"ok": False, "message": str(exc)}
    except Exception as exc:                       # never 500 on a health check
        return 200, {"ok": False,
                     "message": "{}: {}".format(type(exc).__name__, exc)}
    return 200, {"ok": bool(ok), "message": message}


def get_views(config, _body=None):
    return 200, {"views": config.get("views") or []}


def put_views(config, body):
    views = (body or {}).get("views")
    if not isinstance(views, list):
        raise ApiError("views must be a list.")
    cleaned = []
    for v in views[:50]:                       # a cap, so the file cannot grow forever
        if not isinstance(v, dict):
            continue
        cleaned.append({
            "id": str(v.get("id", ""))[:64],
            "name": str(v.get("name", ""))[:80],
            "workspace": str(v.get("workspace", ""))[:32],
            "query": str(v.get("query", ""))[:200],
            "filters": v.get("filters") if isinstance(v.get("filters"), dict) else {},
        })
    config["views"] = cleaned
    state.save(config)
    return 200, {"views": cleaned, "saved": True}


def put_dashboard(config, body):
    """Widget order, visibility, theme and density."""
    state.apply_update(config, "dashboard", (body or {}).get("values") or {})
    state.save(config)
    return 200, {"dashboard": config["dashboard"], "saved": True}


def run_batch(config, body):
    """Stamp, and optionally deliver. The only endpoint that writes anything."""
    if not (body or {}).get("confirm"):
        raise ApiError("A run has to be confirmed.", status=409,
                       detail="This writes files and appends to the ledger.")

    out_dir = _require(config, "paths", "out_dir")
    plan, catalog, warnings, _ = _plan(config)
    assessed, hold_below = _assess(plan, catalog, config, out_dir)
    assessments = {e.get("order_ref"): a for e, a in assessed}

    deliver = _build_delivery(config)
    progress = []

    def note(i, n, name):
        progress.append("[{}/{}] {}".format(i, n, name))

    records, ledger, summary = opus.run_paypal_plan(
        plan, out_dir, progress=note, assessments=assessments, deliver=deliver)

    intact, report = opus.verify_ledger(ledger)
    return 200, {
        "summary": summary,
        "progress": progress,
        "ledger": str(ledger),
        "ledger_intact": intact,
        "ledger_report": report,
        "warnings": warnings,
    }


def _build_delivery(config):
    """A deliver() callable from the saved connector settings, or None."""
    review = config.get("review") or {}
    channels = [c for c in (review.get("deliver_channels") or [])
                if c in ("portal", "smtp")]
    if not channels or not review.get("auto_deliver"):
        return None

    saved = config.get("connectors") or {}
    portal = mailer = None

    if "portal" in channels:
        portal = connectors.get("delivery", "portal")().configure(
            **(saved.get("portal") or {}))
        ok, msg = portal.health()
        if not ok:
            raise ApiError("Delivery is on but the portal is not ready: {}".format(msg),
                           status=409)
    if "smtp" in channels:
        settings = dict(saved.get("smtp") or {})
        settings.setdefault("publisher", config.get("publisher", ""))
        mailer = connectors.get("delivery", "smtp")().configure(**settings)
        ok, msg = mailer.health()
        if not ok:
            raise ApiError("Delivery is on but email is not ready: {}".format(msg),
                           status=409)

    def _deliver(entry, files):
        receipt = portal.deliver(entry, files) if portal else None
        if mailer:
            mailed = mailer.deliver(entry, files, receipt=receipt)
            if receipt is None:
                receipt = mailed
            else:
                receipt = dict(receipt)
                receipt["detail"] += "; " + mailed["detail"]
        return receipt

    return _deliver


def verify_ledger(config, _body=None):
    out_dir = (config.get("paths") or {}).get("out_dir")
    if not out_dir:
        raise ApiError("No output folder configured.", status=409)
    path = opus.ledger_path(out_dir)
    if not path.exists():
        return 200, {"intact": None, "report": ["Nothing issued yet."]}
    intact, report = opus.verify_ledger(path)
    return 200, {"intact": intact, "report": report, "path": str(path)}


def browse(config, body):
    """List a folder, so the UI can offer a picker without a native dialog.

    Deliberately not a general filesystem API: it returns directories and PDFs
    and CSVs only, and it never follows a path it was not asked for.
    """
    raw = (body or {}).get("path") or str(Path.home())
    try:
        here = Path(raw).expanduser()
        if not here.is_absolute():
            here = Path.home() / here
        here = here.resolve()
    except (OSError, RuntimeError):
        raise ApiError("Cannot read that path.")

    if not here.is_dir():
        here = here.parent if here.parent.is_dir() else Path.home()

    entries = []
    try:
        for child in sorted(here.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child), "kind": "dir"})
            elif child.suffix.lower() in (".pdf", ".csv", ".json"):
                entries.append({"name": child.name, "path": str(child),
                                "kind": child.suffix.lower().lstrip(".")})
            if len(entries) >= 500:
                break
    except PermissionError:
        raise ApiError("No permission to read {}".format(here), status=403)

    return 200, {
        "path": str(here),
        "parent": str(here.parent) if here.parent != here else "",
        "entries": entries,
        "home": str(Path.home()),
    }


ROUTES = {
    ("GET", "/api/status"): get_status,
    ("GET", "/api/settings"): get_settings,
    ("POST", "/api/settings"): put_settings,
    ("GET", "/api/orders"): get_orders,
    ("POST", "/api/threshold-preview"): preview_threshold,
    ("GET", "/api/catalog"): get_catalog,
    ("GET", "/api/ledger"): get_ledger,
    ("POST", "/api/ledger/verify"): verify_ledger,
    ("GET", "/api/connectors"): get_connectors,
    ("POST", "/api/connectors/test"): test_connector,
    ("GET", "/api/views"): get_views,
    ("POST", "/api/views"): put_views,
    ("POST", "/api/dashboard"): put_dashboard,
    ("POST", "/api/run"): run_batch,
    ("POST", "/api/browse"): browse,
}


def dispatch(method, path, body, config):
    """Route one call. Returns (status, payload)."""
    handler = ROUTES.get((method, path))
    if handler is None:
        return 404, {"error": "No such endpoint", "detail": "{} {}".format(method, path)}
    try:
        return handler(config, body)
    except ApiError as exc:
        return exc.status, {"error": exc.message, "detail": exc.detail}
    except connectors.ConnectorError as exc:
        return 400, {"error": str(exc), "detail": ""}
    except Exception as exc:
        return 500, {"error": "{}: {}".format(type(exc).__name__, exc),
                     "detail": traceback.format_exc(limit=3)}
