# Oversight: scoring, delivery, and a ledger you can trust

Phase 3. Opus could already match a day of orders and stamp them. What it could
not do was say which orders it was *unsure* about, get the files to the buyer,
or prove afterwards that its own record had not been edited.

Those three gaps are the difference between a tool someone runs and a tool
someone relies on.

---

## Confidence

Opus has always had exactly one confidence number in it: the 0.86 similarity
cutoff below which a title is reported rather than guessed at. This
generalises that into a handful of signals, weighed together.

| Signal | Weight | What it asks |
|---|---|---|
| `match` | 0.45 | Did the title resolve exactly, by containment, or fuzzily? |
| `unambiguous` | 0.20 | Could it have meant a different piece? |
| `files` | 0.20 | Did it resolve to a plausible set of files? |
| `known_buyer` | 0.10 | Have we issued to this buyer before? |
| `contactable` | 0.05 | Is there an address to deliver to? |

```bash
python3 opus.py --demo --out ./licensed --dry-run --confidence --explain
```

```
CONFIDENCE  (release at 1.01 or better)
  0.81   hold      First Baptist Church, Spri Evening Bells - PDF Download
  0.90   hold      Maria Delgado              Fanfare for Two Trumpets
  0.81   hold      Northside High School Band EVENING BELLS (score & parts)
  0.05   reject    Grace Chapel               Processional in D
  -> 0 release, 3 hold, 1 reject
```

**Every signal is computed from data Opus already holds, and there is no
model.** A score you cannot explain to the publisher whose business it is
would be worse than no score, so `--explain` prints the reasoning for every
order and the drawer in the demo shows the same breakdown.

### The default holds everything

`--hold-below` defaults to 1.01, which no order can reach. **Automation is
opt-in, one rung at a time.** Nothing releases itself until someone sets a
threshold, and the right way to set one is on evidence:

```bash
# what would have released last month, at various rungs?
python3 opus.py --paypal last-month.csv --catalog map.csv \
    --out ./licensed --dry-run --confidence --hold-below 0.85
```

Compare that against what a person actually approved. When the two agree often
enough, and you can explain the disagreements, lower the number.

### Three verdicts, and two hard gates

- **release** — score cleared the threshold.
- **hold** — recorded, not stamped, not delivered. A person decides.
- **reject** — nothing to stamp: no catalogue entry matched, or no files.

Two conditions hold an order regardless of its score, because a number should
never be able to talk you past them:

- the transaction is **already in the ledger** — the duplicate guard that makes
  overlapping exports safe;
- more than one catalogue entry could match. The engine quietly takes the
  longest, which is a reasonable default and an invisible risk. Now it is
  visible.

### Held orders are still recorded

A held order writes a ledger row with `status: held`, its score, and its
reason — no file, no delivery. **The ledger answers "why did this not go out?"
as well as "what went out?"**, which is the question you actually have when a
buyer emails asking where their music is.

---

## Delivery

```bash
python3 opus.py --paypal export.csv --catalog map.csv --out ./licensed \
    --hold-below 0.85 \
    --deliver portal,smtp \
    --portal-root ./portal --portal-url https://opus.example.org \
    --smtp-host smtp.example.org --smtp-user pub@example.org \
    --smtp-from pub@example.org --publisher "Fictitious Editions"
```

Both channels together is the intended shape: the portal publishes the files
and the email carries the link.

### Why a link, not an attachment

Three reasons that compound. A twenty-part band piece will not fit in a mail
attachment. A new sender pushing PDFs is spam-filter bait, and a licence in a
junk folder becomes a support call. And a link produces **download telemetry**,
which is itself a licensing signal — one licensee pulling a part eleven times
from four countries is something a publisher wants to know.

`--attach` sends the files instead, and refuses when a batch is large enough
that a mail server will reject it rather than letting you find out from the
buyer.

### The portal

A 128-bit random token names a drop; its manifest lives beside the files,
server-side. **There is no signing key, because there is nothing to sign** — the
token *is* the capability. So there is also no secret to leak, rotate, or
accidentally commit. Expiry is checked against the manifest, never against
anything the client sends, so a link cannot be extended by editing it.

```bash
# serve a portal folder
python3 opus.py --serve-portal ./portal --port 8080

# delete drops past their expiry
python3 opus.py --purge-expired ./portal
```

An expired link that still serves files is not an expiring link, so purge
belongs on a schedule.

**What the bundled server is not:** a CDN or a public file host. It is
stdlib `http.server`, which is right for a desktop agent or a small internal
box. On the open internet it belongs behind a real reverse proxy.

### Email

Ordinary SMTP, which both Gmail and Microsoft 365 accept with an app password.
That avoids an OAuth consent screen, a verification review, and a token
refresh path — the same trade the Drive connector makes, for the same reasons.

An app password is still a password. Use `OPUS_SMTP_PASSWORD` rather than
`--smtp-password`, which lands in shell history.

### A failed send does not lose the files

Delivery runs after stamping, and a failure is recorded against the order
rather than ending the batch. Files that exist but were not sent are
recoverable; a half-finished run is not.

---

## The ledger, as evidence

The ledger's whole purpose is to tie a leaked copy back to an order. A record
anyone can quietly edit is not much of a record, so every row now commits to
the row before it:

```
row_hash = sha256(prev_hash + every signed column)
```

Editing a row, deleting one, reordering two, or inserting a forgery all break
every hash from that point on.

```bash
python3 opus.py --verify-ledger ./licensed/license_ledger.csv
```

```
9 row(s), 9 chained, chain intact.
```

Tampered:

```
9 row(s), 1 problem(s) found.
row 4: contents changed since it was written (c1974568c588... does not match 69bcbe1d381f...)
```

A ledger written before chaining existed reports as **unchained**, not
tampered — the absence of evidence is not evidence of tampering, and crying
wolf on every historic file would make the check worthless.

### What the chain does and does not do

It detects **tampering**. It does not prevent it, and it cannot stop someone
who holds the file from rewriting the whole chain from scratch. Detecting
casual edits — the realistic threat inside a small business — is what it is
for. Off-site backups are what protect against the determined case, and the
chain makes it possible to tell which copy is the honest one.

---

## New columns

| Column | Written when |
|---|---|
| `confidence` | scoring ran |
| `decision` | scoring ran — `release`, `hold`, or `reject` |
| `delivered_at` | delivery succeeded |
| `delivery_channel` | delivery succeeded — `portal` or `smtp` |
| `delivery_ref` | the link, or the address it went to |
| `prev_hash`, `row_hash` | always |

Older ledgers keep working. `append_ledger` ignores columns a record does not
carry, and verification handles a file that has none of them.

---

## Where this leaves the ladder

```
Stage 1  batch and ledger                      done
Stage 2  connected sources, human approves     done
Stage 3  unattended stamping, human approves   the mechanism now exists
         the send
Stage 4  confidence-gated release              --hold-below is the dial
Stage 5  supervised autonomy                   needs agreement data first
```

Stages 4 and 5 are deliberately **not** a code change from here — they are the
same code with a lower number, turned when the evidence supports it. What is
still missing is the evidence itself: a record of how often the automatic
decision agreed with the human one. That is the next thing worth building, and
it is worth building *before* anyone lowers the threshold in anger.

## Tests

```bash
python3 tests/test_phase3.py     # 80 checks
python3 tests/test_connectors.py # 84 checks
```

Delivery is tested against a real local HTTP server and a real local SMTP
server, not mocks — both protocols are where delivery actually goes wrong, and
a mock returning what you told it to would prove nothing. Ledger integrity is
tested by mutating a real ledger four different ways and confirming each is
caught.
