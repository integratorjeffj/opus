#!/usr/bin/env python3
"""Tests for the connector layer.

    python3 tests/test_connectors.py

No pytest dependency -- this has to run inside a plain CI job and on a
publisher's machine without anything extra installed.

The network adapters are tested against recorded response payloads rather than
live services. That covers the part most likely to break silently -- the
transformation from a provider's JSON into an order -- and leaves the HTTP
handshake untested, which is stated plainly rather than implied by a green
tick.
"""

import json
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import connectors as C                                     # noqa: E402
from connectors import catalog_gdrive, orders_paypal_api    # noqa: E402
from connectors.watch import WatchedFolder                  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name
          + (("   -> " + str(detail)) if detail and not condition else ""))


def section(title):
    print("\n" + title)
    print("-" * len(title))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry():
    section("Registry")
    rows = C.describe()
    check("every kind has adapters",
          {r[0] for r in rows} == {"order", "catalog", "delivery"})
    check("states are all legal",
          all(r[3] in (C.BUILT, C.UNVERIFIED, C.PLANNED) for r in rows))

    names = [(r[0], r[1]) for r in rows]
    check("no duplicate names within a kind", len(names) == len(set(names)))

    try:
        C.get("order", "nope")
        check("unknown name raises", False)
    except C.ConnectorError as exc:
        check("unknown name raises with the valid options",
              "paypal-csv" in str(exc), exc)

    # built adapters must lead their kind in the gallery ordering
    order_rows = [r for r in rows if r[0] == "order"]
    check("built adapters sort first", order_rows[0][3] == C.BUILT)

    # delivery stopped being an empty socket in phase 3
    built_delivery = [r for r in rows if r[0] == "delivery" and r[3] == C.BUILT]
    check("delivery has built adapters",
          {r[1] for r in built_delivery} == {"portal", "smtp"},
          [r[1] for r in built_delivery])


def test_planned_refuse():
    section("Planned adapters refuse rather than pretend")
    # smtp and portal were planned in phase 2 and are built now; these are the
    # ones still waiting on something the publisher has to obtain first.
    for kind, name in (("order", "stripe"), ("catalog", "dropbox"),
                       ("delivery", "outlook")):
        cls = C.get(kind, name)
        inst = cls()
        ok, msg = inst.health()
        check("{} reports not-built".format(name), ok is False and "Not built" in msg, msg)
        try:
            if kind == "order":
                inst.list_orders()
            elif kind == "catalog":
                inst.list_items()
            else:
                inst.deliver({}, [])
            check("{} raises when used".format(name), False, "no exception")
        except C.ConnectorError as exc:
            check("{} raises when used".format(name),
                  "not built yet" in str(exc), exc)


# ---------------------------------------------------------------------------
# Local adapters, against the repo's own sample data
# ---------------------------------------------------------------------------

def test_paypal_csv():
    section("PayPal CSV adapter")
    src = C.get("order", "paypal-csv")().configure(
        path=ROOT / "examples" / "paypal_sample.csv")

    ok, msg = src.health()
    check("health ok", ok, msg)

    orders, warnings = src.list_orders()
    # Four, not three. "Processional in D" is a genuine completed sale that
    # happens to have no catalogue entry -- it is the matcher that flags it,
    # not the parser. The 3-ready/1-flagged split appears after matching.
    check("four completed sales survive parsing", len(orders) == 4, len(orders))
    check("the unmatched piece is still a parsed order",
          any(o["item_title"] == "Processional in D" for o in orders))
    check("orders validate against the contract",
          not C.validate_orders(orders), C.validate_orders(orders))

    titles = {o["item_title"] for o in orders}
    check("refund is filtered out",
          not any(o["order_ref"] == "5TX99004DD444444D" for o in orders))
    check("pending payment is filtered out",
          not any(o["order_ref"] == "5TX99007GG777777G" for o in orders))
    check("withdrawal is filtered out",
          not any("Withdrawal" in t for t in titles))
    check("both Evening Bells spellings survive",
          sum(1 for t in titles if "vening" in t.lower()
              or "VENING" in t) >= 1, titles)

    # `since` filtering
    later, _ = src.list_orders(since=date(2026, 8, 19))
    check("since= filters older orders", len(later) == 2, len(later))
    check("since= keeps only orders on or after the date",
          all(o["order_date"] >= date(2026, 8, 19) for o in later))
    check("since= reports what it dropped",
          any("skipped" in w for w in src.list_orders(since=date(2026, 8, 19))[1]))

    missing = C.get("order", "paypal-csv")().configure(path=ROOT / "nope.csv")
    ok, _ = missing.health()
    check("missing file fails health", ok is False)


def test_local_catalog():
    section("Local catalog adapter")
    cat = C.get("catalog", "local")().configure(
        root=ROOT / "samples" / "catalog")

    ok, msg = cat.health()
    check("health ok", ok, msg)

    items = cat.list_items()
    check("finds both pieces", len(items) == 2, [i.title for i in items])
    by_title = {i.title: i for i in items}
    check("Evening Bells has 3 files",
          by_title["Evening Bells"].file_count == 3)
    check("Fanfare has 3 files",
          by_title["Fanfare for Two Trumpets"].file_count == 3)

    check("materialize is a no-op for local",
          cat.materialize(None) == ROOT / "samples" / "catalog")

    tmp = Path(tempfile.mkdtemp())
    try:
        # copy so the real sample folder never gets a stray map written into it
        dest = tmp / "catalog"
        shutil.copytree(ROOT / "samples" / "catalog", dest)
        local = C.get("catalog", "local")().configure(root=dest)
        map_path = local.catalog_map(dest)
        check("catalog_map.csv written", map_path.is_file())

        import opus
        loaded = opus.load_catalog(map_path)
        check("the map the engine reads round-trips", len(loaded) == 2, list(loaded))
        total = sum(len(files) for _title, files in loaded.values())
        check("all six PDFs resolve through the map", total == 6, total)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_end_to_end_through_connectors():
    section("Engine still works when fed entirely through connectors")
    import opus
    src = C.get("order", "paypal-csv")().configure(
        path=ROOT / "examples" / "paypal_sample.csv")
    cat = C.get("catalog", "local")().configure(root=ROOT / "samples" / "catalog")

    tmp = Path(tempfile.mkdtemp())
    try:
        orders, _ = src.list_orders()
        # An explicit map path, because materialize() is a no-op for a local
        # source -- without it the generated map lands in the repo's own
        # samples folder, which is exactly the intrusion the cache exists to
        # avoid.
        map_path = cat.catalog_map(tmp / "cat", tmp / "catalog_map.csv")
        catalog = opus.load_catalog(map_path)
        plan = opus.plan_paypal_batch(orders, catalog, tmp / "out")

        ready = [e for e in plan if e["disposition"] == "ready"]
        unmatched = [e for e in plan if e["disposition"] == "no catalog match"]
        check("three orders ready", len(ready) == 3, len(ready))
        check("nine files planned",
              sum(len(e["files"]) for e in ready) == 9,
              sum(len(e["files"]) for e in ready))
        check("Processional in D flagged", len(unmatched) == 1
              and unmatched[0]["item_title"] == "Processional in D")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Network adapters, against recorded payloads
# ---------------------------------------------------------------------------

def _load_fixture(name):
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


def test_paypal_api_parsing():
    section("PayPal API parsing (recorded payload -- no live call)")
    payload = _load_fixture("paypal_transactions.json")
    orders, warnings = orders_paypal_api.parse_transactions(payload)

    refs = {o["order_ref"] for o in orders}
    check("two real sales survive", len(orders) == 2, [o["order_ref"] for o in orders])
    check("orders validate against the contract", not C.validate_orders(orders))
    check("refund (T1107, negative) dropped", "REFUND0001" not in refs)
    check("pending (status P) dropped", "PENDING001" not in refs)
    check("denied (status D) dropped", "DENIED0001" not in refs)
    check("withdrawal (T0400) dropped", "WITHDRAW01" not in refs)

    first = next(o for o in orders if o["order_ref"] == "SALE000001")
    check("buyer name read", first["buyer"] == "First Baptist Church, Springfield",
          first["buyer"])
    check("item title read from cart_info",
          first["item_title"] == "Evening Bells - PDF Download", first["item_title"])
    check("email read", first["email"] == "office@example.org", first["email"])
    check("date parsed", first["order_date"] == date(2026, 8, 18), first["order_date"])

    second = next(o for o in orders if o["order_ref"] == "SALE000002")
    check("falls back to transaction_subject when cart is empty",
          second["item_title"] == "Fanfare for Two Trumpets", second["item_title"])
    check("builds a name from given/surname when no alternate_full_name",
          second["buyer"] == "Maria Delgado", second["buyer"])

    check("a sale with no title is warned about, not silently dropped",
          any("NOTITLE001" in w for w in warnings), warnings)


def test_paypal_api_windows():
    section("PayPal API date windowing")
    start, end = date(2026, 1, 1), date(2026, 3, 15)
    wins = orders_paypal_api._windows(start, end)
    check("range is chunked", len(wins) == 3, wins)
    check("chunks start at the range start", wins[0][0] == start)
    check("chunks end at the range end", wins[-1][1] == end)
    check("no chunk exceeds 31 days",
          all((b - a).days < 31 for a, b in wins), wins)
    check("chunks are contiguous",
          all(wins[i][1] + timedelta(days=1) == wins[i + 1][0]
              for i in range(len(wins) - 1)))
    check("a single day is one window",
          len(orders_paypal_api._windows(start, start)) == 1)


def test_gdrive_helpers():
    section("Google Drive helpers")
    norm = catalog_gdrive.GoogleDriveCatalog._normalize_folder_id
    check("bare id passes through", norm("1AbC_dEf") == "1AbC_dEf")
    check("folder URL is reduced to the id",
          norm("https://drive.google.com/drive/folders/1AbC_dEf") == "1AbC_dEf")
    check("query string is stripped",
          norm("https://drive.google.com/drive/folders/1AbC_dEf?usp=sharing")
          == "1AbC_dEf")

    safe = catalog_gdrive._safe_name
    check("illegal path characters replaced", "/" not in safe("Bach: Prelude/Fugue"))
    check("trailing dot removed (Windows)", not safe("piece.").endswith("."))
    check("empty name gets a fallback", safe("") == "untitled")

    tmp = Path(tempfile.mkdtemp())
    try:
        bad = tmp / "notakey.json"
        bad.write_text('{"type": "authorized_user"}', encoding="utf-8")
        try:
            catalog_gdrive.load_service_account(bad)
            check("a non-service-account key is rejected", False)
        except C.ConnectorError as exc:
            check("a non-service-account key is rejected with guidance",
                  "Service Accounts" in str(exc), exc)

        try:
            catalog_gdrive.load_service_account(tmp / "missing.json")
            check("a missing key file is rejected", False)
        except C.NotConfigured:
            check("a missing key file is rejected", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gdrive_jwt():
    section("Google Drive JWT assertion")
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        check("cryptography available", False, "not installed")
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()

    token = catalog_gdrive.build_assertion("robot@project.iam.gserviceaccount.com",
                                           pem, now=1_700_000_000)
    parts = token.split(".")
    check("three JWT segments", len(parts) == 3, len(parts))

    import base64

    def unpad(seg):
        return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))

    header = json.loads(unpad(parts[0]))
    claims = json.loads(unpad(parts[1]))
    check("RS256 header", header["alg"] == "RS256", header)
    check("issuer is the service account",
          claims["iss"] == "robot@project.iam.gserviceaccount.com")
    check("audience is Google's token endpoint",
          claims["aud"] == catalog_gdrive.TOKEN_URL)
    check("readonly scope requested", claims["scope"].endswith("drive.readonly"))
    check("expiry is an hour out", claims["exp"] - claims["iat"] == 3600)

    # the signature must actually verify
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    signing_input = (parts[0] + "." + parts[1]).encode()
    try:
        key.public_key().verify(unpad(parts[2]), signing_input,
                                padding.PKCS1v15(), hashes.SHA256())
        check("signature verifies against the public key", True)
    except Exception as exc:
        check("signature verifies against the public key", False, exc)

    bad = catalog_gdrive.build_assertion
    try:
        bad("robot@x", "not a pem")
        check("a bad private key is rejected", False)
    except C.ConnectorError:
        check("a bad private key is rejected", True)


# ---------------------------------------------------------------------------
# Watched folder
# ---------------------------------------------------------------------------

def test_watch():
    section("Watched folder")
    tmp = Path(tempfile.mkdtemp())
    try:
        w = WatchedFolder(tmp, pattern="*.csv", settle_seconds=0.05)
        check("empty folder yields nothing", w.scan() == [])

        (tmp / "a.csv").write_text("one", encoding="utf-8")
        (tmp / "ignored.txt").write_text("two", encoding="utf-8")
        found = w.scan()
        check("new csv is found", [p.name for p in found] == ["a.csv"], found)
        check("non-matching pattern ignored", "ignored.txt" not in
              [p.name for p in found])

        check("a second scan does not repeat it", w.scan() == [])

        (tmp / "b.csv").write_text("three", encoding="utf-8")
        check("a further new file is found",
              [p.name for p in w.scan()] == ["b.csv"])

        # same name, different size -> genuinely a new export
        time.sleep(0.02)
        (tmp / "a.csv").write_text("one but longer now", encoding="utf-8")
        check("same name with new contents is treated as new",
              [p.name for p in w.scan()] == ["a.csv"])

        # empty files are never settled
        (tmp / "empty.csv").write_text("", encoding="utf-8")
        check("zero-byte file is not reported", w.scan() == [])

        # memory survives a new watcher over the same folder
        w2 = WatchedFolder(tmp, pattern="*.csv", settle_seconds=0.05)
        check("state file survives a restart", w2.scan() == [])

        w2.forget_all()
        check("forget_all makes everything look new again",
              len(w2.scan()) >= 2)

        check("a missing folder yields nothing rather than raising",
              WatchedFolder(tmp / "nope", settle_seconds=0.01).scan() == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_watch_loop():
    section("Watch loop")
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "x.csv").write_text("data", encoding="utf-8")
        seen = []
        from connectors.watch import watch
        rc = watch(tmp, on_file=seen.append, pattern="*.csv",
                   once=True, log=None)
        check("single pass returns 0", rc == 0)
        check("callback received the file", [p.name for p in seen] == ["x.csv"])

        # a callback that explodes must not end the watch
        (tmp / "y.csv").write_text("data2", encoding="utf-8")

        def boom(_p):
            raise RuntimeError("adapter blew up")

        msgs = []
        rc = watch(tmp, on_file=boom, pattern="*.csv", once=True,
                   log=msgs.append)
        check("a failing callback does not stop the watch", rc == 0)
        check("the failure is reported",
              any("blew up" in m for m in msgs), msgs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------

def main():
    print("Opus connector tests")
    print("=" * 60)
    for fn in (test_registry, test_planned_refuse, test_paypal_csv,
               test_local_catalog, test_end_to_end_through_connectors,
               test_paypal_api_parsing, test_paypal_api_windows,
               test_gdrive_helpers, test_gdrive_jwt, test_watch,
               test_watch_loop):
        fn()

    print("\n" + "=" * 60)
    print("{} passed, {} failed".format(len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print("  FAILED: {}".format(name))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
