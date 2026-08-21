#!/usr/bin/env python3
"""Tests for the oversight surface: scoring, delivery, ledger integrity.

    python3 tests/test_phase3.py

No pytest, for the same reason as the connector suite: this has to run in a
plain CI job and on a publisher's machine with nothing extra installed.

Delivery is tested against a real local HTTP server and a real local SMTP
server rather than mocks. Both protocols are where delivery actually goes
wrong, and a mock that returns what you told it to would prove nothing.
"""

import csv
import email as emaillib
import json
import socketserver
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import opus                                                    # noqa: E402
import connectors as C                                         # noqa: E402
from connectors import confidence as CF                        # noqa: E402
from connectors.catalog_local import write_catalog_map         # noqa: E402
from connectors.delivery_portal import (build_handler, is_expired,  # noqa: E402
                                        read_manifest)
from connectors.delivery_smtp import SMTPDelivery              # noqa: E402

PASSED, FAILED = [], []

PORTAL_PORT = 8894
SMTP_PORT = 8895


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name
          + (("   -> " + str(detail)) if detail and not condition else ""))


def section(title):
    print("\n" + title)
    print("-" * len(title))


def sample_plan():
    """The real plan the sample export produces, and its catalogue."""
    m = write_catalog_map(ROOT / "samples" / "catalog",
                          Path(tempfile.mkdtemp()) / "m.csv")
    catalog = opus.load_catalog(m)
    orders, _ = opus.read_paypal_orders(ROOT / "examples" / "paypal_sample.csv")
    return opus.plan_paypal_batch(orders, catalog, None), catalog


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def test_confidence():
    section("Confidence scoring")
    plan, catalog = sample_plan()
    assessed = CF.assess_plan(plan, catalog)
    by_ref = {e["order_ref"]: a for e, a in assessed}

    check("weights sum to 1", abs(sum(CF.WEIGHTS.values()) - 1.0) < 1e-9)
    check("the default posture releases nothing",
          CF.summarize(assessed)["release"] == 0, CF.summarize(assessed))
    check("an unmatched title is rejected, not merely held",
          by_ref["5TX99005EE555555E"].verdict == CF.REJECT)
    check("the rejection explains itself",
          any("No catalogue entry matched" in r
              for r in by_ref["5TX99005EE555555E"].reasons))
    check("an exact match outscores a containment match",
          by_ref["5TX99002BB222222B"].score > by_ref["5TX99001AA111111A"].score,
          (by_ref["5TX99002BB222222B"].score, by_ref["5TX99001AA111111A"].score))
    check("every signal carries a note a person could read",
          all(s.note for _e, a in assessed for s in a.signals))

    released = [CF.summarize(CF.assess_plan(plan, catalog, hold_below=t))["release"]
                for t in (1.01, 0.85, 0.80)]
    check("the ladder turns on one number", released == [0, 1, 3], released)

    first = CF.assess_plan(plan, catalog)[1][1]
    again = CF.assess_plan(plan, catalog, known_buyers=["Maria Delgado"])[1][1]
    check("a returning buyer scores higher", again.score > first.score,
          (first.score, again.score))

    dup = CF.assess_plan(plan, catalog, known_refs=["5TX99002BB222222B"],
                         hold_below=0.0)[1][1]
    check("a duplicate transaction is held whatever the score",
          dup.verdict == CF.HOLD, dup.verdict)
    check("and says why",
          any("already in the ledger" in r for r in dup.reasons), dup.reasons)

    amb = CF.assess({"item_title": "Evening Bells", "match": "contains",
                     "matched_title": "Evening Bells", "files": [1, 2, 3],
                     "order_ref": "X", "buyer": "B", "email": "a@b.c"},
                    catalog_titles=["Evening Bells", "Evening Bells (Brass)"],
                    hold_below=0.9)
    check("two possible matches is caught as ambiguity",
          amb.verdict == CF.HOLD
          and any("More than one piece" in r for r in amb.reasons), amb.reasons)

    short = CF.assess({"item_title": "Evening Bells", "match": "exact",
                       "matched_title": "Evening Bells", "files": [1],
                       "order_ref": "Y", "buyer": "B", "email": "a@b.c"},
                      catalog_titles=["Evening Bells"], hold_below=0.9,
                      expected_parts={"Evening Bells": 3})
    check("a short part count is held",
          short.verdict == CF.HOLD
          and any("expected parts" in r for r in short.reasons), short.reasons)

    check("explain() names the verdict", "HOLD" in short.explain())
    check("as_dict is serialisable", json.dumps(short.as_dict())[:1])


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

def _write_rows(rows, fields):
    path = Path(tempfile.mkdtemp()) / "license_ledger.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def test_ledger_chain():
    section("Ledger tamper-evidence")
    tmp = Path(tempfile.mkdtemp())
    recs = [{"stamped_at": "t%d" % i, "licensee": "Buyer %d" % i,
             "order_ref": "R%d" % i, "item_title": "Piece",
             "license_date": "8/1/26", "source_file": "a.pdf",
             "output_file": "b%d.pdf" % i, "pages": "2",
             "owner_password": "pw%d" % i, "status": "ok", "notes": ""}
            for i in range(1, 5)]

    path = opus.append_ledger(tmp, recs[:2])
    opus.append_ledger(tmp, recs[2:])          # a second batch continues the chain

    ok, rep = opus.verify_ledger(path)
    check("a freshly written ledger verifies", ok, rep)
    check("the chain spans separate batches", "4 chained" in rep[0], rep[0])

    rows = list(csv.DictReader(path.open(encoding="utf-8")))

    def mutate(fn):
        return _write_rows(fn([dict(r) for r in rows]), opus.LEDGER_FIELDS)

    cases = {
        "an edited row": lambda rs: [dict(r, licensee="Someone Else")
                                     if i == 1 else r for i, r in enumerate(rs)],
        "a deleted row": lambda rs: [r for i, r in enumerate(rs) if i != 1],
        "reordered rows": lambda rs: [rs[0], rs[2], rs[1], rs[3]],
        "an inserted row": lambda rs: rs[:2] + [dict(rs[1], order_ref="FAKE")] + rs[2:],
    }
    for name, fn in cases.items():
        ok, _rep = opus.verify_ledger(mutate(fn))
        check("{} is detected".format(name), not ok)

    legacy = _write_rows(recs, opus.LEDGER_CORE_FIELDS)
    ok, rep = opus.verify_ledger(legacy)
    check("an unchained legacy ledger is not accused", ok, rep)
    check("but it says so plainly",
          any("predates tamper-evidence" in line for line in rep), rep)

    ok, rep = opus.verify_ledger(
        ROOT / "samples" / "licensed" / "license_ledger.csv")
    check("the committed sample ledger verifies", ok, rep)

    ok, rep = opus.verify_ledger(Path(tempfile.mkdtemp()) / "nothing.csv")
    check("a missing ledger reports rather than raising", not ok and rep)


# ---------------------------------------------------------------------------
# Portal
# ---------------------------------------------------------------------------

def test_portal():
    section("Expiring download portal")
    root = Path(tempfile.mkdtemp())
    ch = C.get("delivery", "portal")().configure(
        root=root, base_url="http://127.0.0.1:{}".format(PORTAL_PORT), ttl_days=14)

    ok, msg = ch.health()
    check("health ok", ok, msg)

    files = sorted((ROOT / "samples" / "licensed").glob(
        "Evening_Bells_*First_Baptist*.pdf"))
    check("three sample files to deliver", len(files) == 3, len(files))

    order = {"order_ref": "R1", "buyer": "First Baptist Church, Springfield",
             "email": "office@example.org", "item_title": "Evening Bells"}
    receipt = ch.deliver(order, files)
    check("the receipt honours the contract",
          all(k in receipt for k in ("channel", "sent_at", "detail")), receipt)
    check("and carries a url and an expiry",
          receipt["url"].startswith("http") and bool(receipt["expires_at"]))
    check("two deliveries get different tokens",
          ch.deliver(order, files)["token"] != receipt["token"])

    srv = ThreadingHTTPServer(("127.0.0.1", PORTAL_PORT), build_handler(root))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def get(path):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:{}{}".format(PORTAL_PORT, path),
                        timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        tok = receipt["token"]
        code, body = get("/d/" + tok)
        check("the landing page serves", code == 200, code)
        check("it lists every file", body.count(b"<li>") == 3, body.count(b"<li>"))
        check("it names the licensee", b"First Baptist" in body)

        quoted = urllib.request.quote(files[0].name)
        code, body = get("/d/{}/{}".format(tok, quoted))
        check("the file downloads", code == 200 and body[:4] == b"%PDF", code)
        check("it is served as an attachment", len(body) > 1000)

        check("a file outside the drop is refused",
              get("/d/{}/other.pdf".format(tok))[0] == 404)
        check("an unknown token is refused", get("/d/deadbeef")[0] == 404)
        check("path traversal is refused", get("/d/../../etc")[0] in (400, 404))
        check("the root serves nothing", get("/")[0] == 404)

        manifest = read_manifest(root / tok)
        check("the download is recorded as telemetry",
              len(manifest["downloads"]) == 1, manifest.get("downloads"))
        check("telemetry records who pulled it",
              manifest["downloads"][0]["client"] == "127.0.0.1")

        manifest["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)
                                  ).replace(microsecond=0).isoformat()
        (root / tok / "manifest.json").write_text(json.dumps(manifest),
                                                  encoding="utf-8")
        check("is_expired agrees", is_expired(read_manifest(root / tok)))
        check("an expired link returns 410", get("/d/" + tok)[0] == 410)
        check("an expired file returns 410 too",
              get("/d/{}/{}".format(tok, quoted))[0] == 410)

        check("purge removes only the expired drop", ch.purge_expired() == 1)
        check("and it is gone", get("/d/" + tok)[0] == 404)
        check("the unexpired drop survives", len(ch.drops()) == 1, len(ch.drops()))
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------

class MailSink:
    """A minimal SMTP server, so delivery is verified rather than assumed."""

    def __init__(self, port):
        self.received = []
        sink = self
        CRLF = "\r\n"

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                def w(text):
                    self.wfile.write((text + CRLF).encode())

                w("220 sink")
                data, in_data, env = [], False, {}
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", "replace").rstrip(CRLF)
                    if in_data:
                        if line == ".":
                            in_data = False
                            env["raw"] = CRLF.join(data)
                            sink.received.append(dict(env))
                            data = []
                            w("250 OK")
                        else:
                            data.append(line[1:] if line.startswith("..") else line)
                        continue
                    upper = line.upper()
                    if upper.startswith(("EHLO", "HELO")):
                        w("250-sink")
                        w("250 AUTH PLAIN LOGIN")
                    elif upper.startswith("AUTH"):
                        w("235 ok")
                    elif upper.startswith("MAIL FROM"):
                        env["from"] = line.split(":", 1)[1].strip()
                        w("250 OK")
                    elif upper.startswith("RCPT TO"):
                        env.setdefault("to", []).append(line.split(":", 1)[1].strip())
                        w("250 OK")
                    elif upper == "DATA":
                        in_data = True
                        w("354 go ahead")
                    elif upper.startswith("QUIT"):
                        w("221 bye")
                        return
                    else:
                        w("250 OK")

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
        self.srv.allow_reuse_address = True

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc):
        self.srv.shutdown()
        self.srv.server_close()


def test_smtp():
    section("Email delivery")
    files = sorted((ROOT / "samples" / "licensed").glob(
        "Evening_Bells_*First_Baptist*.pdf"))
    order = {"order_ref": "R1", "buyer": "First Baptist Church, Springfield",
             "email": "office@example.org", "item_title": "Evening Bells"}
    portal_receipt = {"url": "https://opus.example.org/d/abc",
                      "expires_at": "2026-09-04T00:00:00+00:00"}

    with MailSink(SMTP_PORT) as sink:
        ch = C.get("delivery", "smtp")().configure(
            host="127.0.0.1", port=SMTP_PORT, use_tls=False,
            username="pub@example.org", password="app-password",
            sender="pub@example.org", publisher="Fictitious Editions")

        ok, msg = ch.health()
        check("health ok", ok, msg)

        receipt = ch.deliver(order, files, receipt=portal_receipt)
        check("the receipt honours the contract",
              all(k in receipt for k in ("channel", "sent_at", "detail")), receipt)
        check("one message arrived", len(sink.received) == 1, len(sink.received))

        msg = emaillib.message_from_string(sink.received[-1]["raw"])
        body = msg.get_payload(decode=True).decode()
        check("addressed to the buyer", msg["To"] == "office@example.org")
        check("sent from the publisher", msg["From"] == "pub@example.org")
        check("the publisher is named in the subject",
              "Fictitious Editions" in msg["Subject"], msg["Subject"])
        check("the body carries the portal link", portal_receipt["url"] in body)
        check("the body carries the expiry date", "2026-09-04" in body)
        check("the body names the licensee", order["buyer"] in body)
        check("nothing is attached when a link was sent", not msg.is_multipart())

        ch.configure(attach=True)
        ch.deliver(order, files)
        msg = emaillib.message_from_string(sink.received[-1]["raw"])
        names = [p.get_filename() for p in msg.walk() if p.get_filename()]
        check("attach mode attaches every file", len(names) == 3, names)
        check("attachments keep their filenames",
              all(n.endswith(".pdf") for n in names), names)

        before = len(sink.received)
        dry = ch.deliver(order, files, receipt=portal_receipt, dry_run=True)
        check("a dry run sends nothing", len(sink.received) == before)
        check("and says so", "dry run" in dry["detail"], dry)

        try:
            ch.deliver({"order_ref": "X", "buyer": "B", "email": ""}, files)
            check("an order with no address is refused", False)
        except C.ConnectorError as exc:
            check("an order with no address is refused",
                  "nowhere to send" in str(exc), exc)

        try:
            ch.deliver(order, [Path("does-not-exist.pdf")])
            check("a missing attachment is refused", False)
        except C.ConnectorError as exc:
            check("a missing attachment is refused", "missing file" in str(exc), exc)

    unreachable = SMTPDelivery().configure(host="127.0.0.1", port=9998,
                                           use_tls=False, sender="a@b.c")
    ok, msg = unreachable.health()
    check("an unreachable server fails health rather than raising",
          ok is False and "Could not reach" in msg, msg)


# ---------------------------------------------------------------------------
# The whole thing together
# ---------------------------------------------------------------------------

def test_hold_and_deliver():
    section("Held orders are recorded, not issued")
    plan, catalog = sample_plan()
    tmp = Path(tempfile.mkdtemp())

    assessed = CF.assess_plan(plan, catalog, hold_below=0.85)
    assessments = {e.get("order_ref"): a for e, a in assessed}

    delivered = []

    def deliver(entry, files):
        delivered.append(entry["order_ref"])
        return {"channel": "test", "sent_at": "2026-08-21T00:00:00+00:00",
                "detail": "sent", "url": "https://example.org/d/x"}

    records, ledger, summary = opus.run_paypal_plan(
        plan, tmp, assessments=assessments, deliver=deliver)

    check("only the released order is stamped", summary["orders"] == 1, summary)
    check("two orders are held", summary["held"] == 2, summary)
    check("held files are never written",
          len(list(tmp.glob("*.pdf"))) == 3, len(list(tmp.glob("*.pdf"))))
    check("only released orders are delivered",
          delivered == ["5TX99002BB222222B"], delivered)

    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    held = [r for r in rows if r["status"] == "held"]
    issued = [r for r in rows if r["status"] == "ok"]

    check("held orders still reach the ledger", len(held) == 2, len(held))
    check("each held row carries its score", all(r["confidence"] for r in held))
    check("each held row carries a reason", all(r["notes"] for r in held))
    check("a held row issues no file", all(not r["output_file"] for r in held))
    check("issued rows record the delivery",
          all(r["delivery_ref"] and r["delivered_at"] for r in issued))
    check("issued rows record the decision",
          all(r["decision"] == "release" for r in issued))

    ok, rep = opus.verify_ledger(ledger)
    check("a ledger from a real run verifies", ok, rep)

    # a second pass over the same export must not reissue anything
    plan2, _ = sample_plan()
    plan2 = opus.plan_paypal_batch(
        [dict(e) for e in plan2], catalog, tmp)
    reissued = [e for e in plan2
                if e["order_ref"] == "5TX99002BB222222B"
                and e["disposition"] == "ready"]
    check("an already-issued order is not planned again", not reissued,
          [e["disposition"] for e in plan2])


def test_delivery_failure_is_survivable():
    section("A delivery failure does not lose the files")
    plan, catalog = sample_plan()
    tmp = Path(tempfile.mkdtemp())

    def exploding(entry, files):
        raise RuntimeError("mail server on fire")

    records, ledger, summary = opus.run_paypal_plan(
        plan, tmp, deliver=exploding)

    check("the files were still stamped", summary["files_ok"] == 9, summary)
    check("the batch did not abort", summary["files_failed"] == 0, summary)
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    check("the failure is recorded against the order",
          all("delivery failed" in (r["notes"] or "")
              for r in rows if r["status"] == "ok"))
    check("nothing claims to have been delivered",
          not any(r["delivered_at"] for r in rows))
    ok, _rep = opus.verify_ledger(ledger)
    check("the ledger still verifies", ok)


def main():
    print("Opus phase 3 tests -- scoring, delivery, ledger integrity")
    print("=" * 62)
    for fn in (test_confidence, test_ledger_chain, test_portal, test_smtp,
               test_hold_and_deliver, test_delivery_failure_is_survivable):
        fn()

    print("\n" + "=" * 62)
    print("{} passed, {} failed".format(len(PASSED), len(FAILED)))
    for name in FAILED:
        print("  FAILED: {}".format(name))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
