# Opus

**Licensing stamper for small sheet music publishers.**

Stamps every page of a PDF with the buyer's name, locks the file against
editing, and records the copy in a permanent ledger. Reads a PayPal export and
processes a day of orders in one pass.

**→ [Try the app in your browser](https://integratorjeffj.github.io/opus/demo/) · [How it works](https://integratorjeffj.github.io/opus) · [Sample output](samples/licensed)**

> [!IMPORTANT]
> **Demo data only.** This is a portfolio build. Point it at fictional,
> redacted, or otherwise non-sensitive files — never a real PayPal export or
> music you don't hold the rights to. Opus asks you to confirm this before it
> will write anything. See [Using it safely](#using-it-safely).

---

## Why it's called Opus

An opus number is how a composer's work gets catalogued. Op. 27, No. 2 isn't a
description of the music — it's an identifier, assigned once, that lets anyone
say precisely which piece they mean two hundred years later. Publishers have
been numbering works this way since the seventeenth century for one reason:
without a catalogue, you cannot tell one copy of a thing from another.

That is exactly the problem here, moved down a level. A publisher doesn't just
need to know which *piece* went out — they need to know which *copy*, to whom,
under which order, on what date. So Opus does for copies what opus numbers did
for works: every file it issues gets a name on the page and a row in the
ledger, and stays identifiable for as long as it exists.

The pun is intentional. It's also literally what the tool does.

---

## The problem

A small publisher sells engraved PDFs. Anyone can forward a PDF, so each copy
goes out stamped with the purchaser's name. That part was already solved by a
script: one file, one command, one licensee typed by hand.

Everything around it was manual. An order arrives, and someone has to notice
it, find the right folder, retype the buyer's name once per file, run the
command once per file, find the outputs, and email them back. A choral octavo
is a score, a choral score, and an organ part. A band piece can be twenty
parts. The buyer's name gets typed twenty times, and the twentieth one has a
typo in it.

Worse, nothing was written down. The script generated an owner password, printed
it to the terminal, and that was the last anyone saw of it. If a PDF turned up
on a file-sharing site, there was no record tying the name on the page to an
order.

## What Opus does

| Before | After |
|---|---|
| One file per command | A day of orders in one pass |
| Buyer name typed once per file | Typed once per order, or read from PayPal |
| No record of what was issued | Every copy logged with buyer, order ID, and date |
| Overlapping exports reissue the same files | Already-issued orders detected and skipped |
| Refunds processed as sales | Refunds, withdrawals, and pending payments filtered out |

## How an order flows

1. **Read the export.** PayPal's activity CSV. Column names are matched by
   meaning rather than position, because PayPal has renamed its export headers
   several times. Only completed payments survive: refunds, reversals,
   withdrawals, fees, and pending rows are dropped.
2. **Match the title to files.** `"Evening Bells - PDF Download"` and
   `"EVENING BELLS (score & parts)"` both resolve to the same catalog entry.
   Titles are normalized, then matched exact, then by containment, then fuzzy
   at 86% similarity. Anything below that is reported as unmatched rather than
   guessed at, because issuing the wrong piece is worse than issuing nothing.
3. **Check the ledger.** Transaction IDs already recorded as issued are
   skipped. This is the failure mode that would otherwise bite hardest, since
   export date ranges overlap constantly.
4. **Review.** A table of every order with its match, file count, and
   disposition. Nothing is written until it's approved.
5. **Stamp, lock, log.** Per file: notice merged into the page content stream,
   form fields flattened, AES-256 encryption applied with editing and copying
   blocked, and a ledger row appended.

## The stamp

A notice across the top of every page and the purchase date across the bottom:

> This music is licensed for use by First Baptist Church, Springfield.
> Distribution and/or use otherwise is prohibited by federal law.

The text is merged into each page's content stream rather than added as an
annotation, so it is not a separate object a reader can select and delete.
Rotated and landscape pages are detected and the notice is rotated to match, so
it always reads the same way up as the music.

Files open and print with no password. Editing, text extraction, commenting,
and page re-assembly are blocked.

**What this does not do:** stop screenshots, phone photos, or re-printing to a
new PDF. No PDF tool can. The deterrent is the name on the page. A shared copy
stays traceable to whoever shared it, which is the point.

## The ledger

Every issued file appends a row to `license_ledger.csv`:

```
stamped_at, licensee, order_ref, item_title, license_date,
source_file, output_file, pages, owner_password, status, notes
```

This is the piece the original script was missing entirely, and it does three
jobs at once: it makes a leaked copy traceable back to an order, it prevents
duplicate issuance, and it is the publisher's record of what has been licensed.

## Output naming

```
Evening_Bells_organ__First_Baptist_Church_Springfield.pdf
└─── piece ───┘└part┘  └────── licensee ──────────┘
```

The piece name is included because nearly every product folder contains a file
called `score.pdf`. Without it, a buyer who orders two pieces receives
`score__Name.pdf` and `score__Name_2.pdf` with no way to tell them apart.
Nothing is ever overwritten.

## Using it safely

Opus is published as a demonstration. The inputs it expects are exactly the
sensitive kind: a payment export full of buyer names, email addresses and
transaction IDs, and a folder of copyrighted engravings. So before it writes a
single file it makes you say that what you've loaded is safe to load.

- **App window.** A dialog opens first, ahead of the main window. Nothing can
  be queued or browsed until the box is ticked; `Continue` stays greyed out
  until then, and `Quit` closes the app.
- **Command line.** Any run that actually stamps something prompts for
  confirmation first. `--dry-run` is exempt, because it writes nothing.
- **Scripted runs.** Pass `--demo-ack`, or set `OPUS_DEMO_ACK=1`, to confirm up
  front without a prompt. With no terminal to prompt at and no acknowledgement
  given, Opus refuses the run rather than assuming consent.

What to use instead of real data: `examples/` for a messy-but-fictional PayPal
export, and `samples/catalog/` for a small catalogue of fictional scores. Both
ship in this repo.

## Try it without installing anything

**[Open the licensing desk](https://integratorjeffj.github.io/opus/demo/)** —
a browser walkthrough of a working day: the demo-data gate, four completed
sales matched against the catalogue, the one order that needs a person, the
stamping run, and the ledger it writes — ending on a real stamped page.

It is a simulation of the desktop app, and it says so: nothing is uploaded and
no PDF is produced in your browser. Every number, match, order reference,
filename and ledger row in it is the actual output of running Opus against the
fictional catalogue in this repository, generated by `packaging/build_demo.py`
so it cannot drift from what the tool really does.

Worth clicking:

- **Any order row** — the drawer shows what PayPal sent, what it resolved to,
  and the exact files that order issues.
- **The flagged order** — `Processional in D` explains *why* nothing was
  chosen, rather than only reporting that nothing matched.
- **Ledger search** — type `delgado` and watch nine rows narrow to three.
- **Any ledger row** — the licence notice at readable size, and the master
  page beside the issued one.

### One interface, two backends

`docs/demo/` is **generated from the app's own interface**, not written
separately. `packaging/build_demo.py` inlines the real `webui/static/app.html`,
`app.css` and `app.js` and swaps in a mock adapter answering the same endpoints
the local server does, from data captured by running the engine.

That is deliberate. When the demo and the app were separate files, the demo got
the design attention and the app got the features, until the polished one could
not do anything and the capable one looked like 1998. CI now regenerates the
demo and fails if the result differs from what is committed, so that drift
cannot happen again.

### What changed from v1 to v2

The first demo drew a fake desktop window and replayed a single linear script.
It was accurate, but it read as a screenshot of an app rather than a product,
and it hid the most interesting thing the engine does.

| | v1 | v2 |
|---|---|---|
| Framing | Simulated OS window | App shell with a sidebar |
| Navigation | Four tabs, one linear path | Five workspaces, switched by state |
| Opening view | An empty input form | Six stat tiles from real counts |
| The exception | Row four of a table | Its own card, with the reason and a jump to the order |
| Filtered rows | Invisible | Listed, with why each was dropped |
| Title matching | Not shown | Every order shows exact / contains / no match |
| Detail | One preview at the bottom | A drawer, from orders and ledger rows alike |
| Ledger | A static dump | Searchable, with per-row detail |

The single biggest gain cost nothing to compute: `plan_paypal_batch` already
returned *how* each title matched, and the demo generator was discarding it.
`Evening Bells - PDF Download` and `EVENING BELLS (score & parts)` collapsing
onto one catalogue entry is the argument for the whole tool, and it is now the
most visible thing on the screen.

No invented metrics were added. There is no revenue tile, because Opus does not
track revenue.

## Getting it

**[Download the latest release](https://github.com/integratorjeffj/opus/releases/latest)**
-- `Opus.exe` for Windows, `Opus-macos.zip` for macOS. One file, no Python, no
install. Double-click it.

The fictional catalogue is bundled inside the app, so you can open the PayPal
Orders tab, press **Try it with the sample order**, and watch stamped and
locked PDFs come out without touching a real order or downloading anything
else.

Builds are unsigned until certificates are in place, so the first run shows a
SmartScreen warning on Windows, and macOS Gatekeeper will refuse the app
outright. [BUILDING.md](BUILDING.md) covers what signing costs and how the
release pipeline handles it.

## Running it from source

```bash
pip3 install pypdf reportlab pikepdf cryptography
python3 opus.py
```

That opens the same app window. The command line is there for scripting and
testing:

```bash
# Build a catalog map by scanning a folder of pieces
python3 opus.py --make-catalog ./catalog -o catalog_map.csv

# See what a PayPal export would produce, without writing anything
python3 opus.py --paypal examples/paypal_sample.csv \
    --catalog examples/catalog_map.csv --out ./licensed --dry-run

# One-off license outside of PayPal
python3 opus.py --licensee "Grace Chapel" --out ./licensed \
    --folder "./catalog/Evening Bells"

# Run the whole bundled demo, start to finish
python3 opus.py --demo --out ./licensed --demo-ack
```

`qpdf` is optional. Without it the extra form-flatten pass is skipped, which is
fine for engraved music that has no form fields.

## Try it

`examples/paypal_sample.csv` is a deliberately messy export: two title
variants for the same piece, a refund, a general withdrawal, a pending payment,
and an order for a piece that isn't in the catalog. Run the dry-run command
above and every one of those is handled or flagged.

`samples/catalog/` holds the fictional scores — two pieces, six PDFs, engraved
for this demo and marked as such on every page. `samples/licensed/` is the
output of running the command above for real: nine stamped, locked files plus
the ledger that recorded them. Both are committed, so you can open a stamped
PDF without installing anything.

Every name, title, transaction ID and note in those folders is invented. The
owner passwords in the sample ledger unlock nothing outside this repo.

## Where orders and files come from

Opus reads orders from a **source** and masters from a **catalog**, and both
are pluggable. A downloaded PayPal export and a local folder are what ship
working today; PayPal's live API and a shared Google Drive folder are
implemented but not yet run against the live services.

```bash
python3 opus.py --list-connectors
```

Anything listed as `planned` is a contract with nothing behind it, and
selecting one is an error rather than a quiet no-op — so nothing in the app
can look connected when it is not. **[CONNECTORS.md](CONNECTORS.md)** covers
setup for each, including why Drive uses a service account instead of a
"Sign in with Google" button.

There is also a watched-folder mode: drop an export into a folder and Opus
plans the batch. It plans; it does not stamp. A human still approves.

## Deciding what still needs a person

Opus scores every order before it stamps anything, from signals it already
holds: how the title matched, whether more than one piece could have matched,
whether the file count looks right, whether this buyer has ordered before.
There is no model, and `--explain` prints the reasoning for every order.

```bash
python3 opus.py --demo --out ./licensed --dry-run --confidence --explain
```

**The default holds everything.** Automation is opened one rung at a time, by
lowering `--hold-below`, and the right way to pick a number is to compare what
*would* have released against what a person actually approved. A held order is
still written to the ledger with its score and its reason, so the record
answers "why did this not go out?" as well as "what went out?".

Two things hold an order regardless of score: a transaction already in the
ledger, and a title that more than one catalogue entry could match.

## Delivering it

```bash
python3 opus.py --paypal export.csv --catalog map.csv --out ./licensed     --hold-below 0.85 --deliver portal,smtp     --portal-root ./portal --portal-url https://opus.example.org     --smtp-host smtp.example.org --smtp-user pub@example.org
```

The portal publishes the files under an expiring link and the email carries
that link — not the files. A twenty-part band piece does not fit in an
attachment, a new sender pushing PDFs is spam-filter bait, and a link produces
download telemetry that is itself a licensing signal.

## A ledger you can check

Every ledger row commits to the row before it, so editing one, deleting one,
reordering two or inserting a forgery all break the chain from that point on.

```bash
python3 opus.py --verify-ledger ./licensed/license_ledger.csv
```

It detects tampering; it does not prevent it. Detecting casual edits is the
realistic threat inside a small business, and off-site backups are what cover
the determined case.

**[OVERSIGHT.md](OVERSIGHT.md)** covers all three in full.

## Tests

```bash
python3 tests/test_connectors.py   # 84  sources, catalogues, the watched folder
python3 tests/test_phase3.py       # 80  scoring, delivery, ledger integrity
python3 tests/test_webui.py        # 122 settings, the API, the server's guards
```

286 checks, no pytest required, all three run in CI on both platforms along
with a rebuild of the demo to prove it still matches the interface.

Nothing is mocked where a real thing would do: delivery is tested against a
local HTTP server and a local SMTP server, the app's guards are tested over
real sockets, and ledger tamper-detection works by mutating a real ledger four
ways and confirming each is caught.

## Roadmap

- [x] Watch folder, so dropping an export in plans the batch on a schedule
- [x] Packaged `.app` and `.exe` via PyInstaller, removing the Python install
- [ ] Code signing and notarization, so a first run is not a warning
- [ ] Per-page notice placement rules for pieces with tight title blocks
- [x] Connector layer, so a new source is an adapter rather than a rewrite
- [ ] Run the PayPal API and Google Drive adapters against live accounts
- [ ] Shopify and Square adapters alongside PayPal
- [x] Delivery: expiring download links and mail, recorded in the ledger
- [x] Confidence scoring, so automation can be opened one rung at a time
- [x] Tamper-evident ledger
- [x] Browser interface, so nothing needs a terminal
- [ ] Agreement tracking: how often the automatic decision matched the human one
- [ ] Retire the older tkinter window

## Notes

Built for a working publisher, then genericized for release. All names, titles,
transaction IDs, and sample music here are fictional.

Python 3.9+. `pypdf`, `reportlab`, `pikepdf`. MIT licensed.

---

### Also built

**[Flowline](https://github.com/integratorjeffj/flowline)** · AI-driven task prioritization dashboard
**[Foreman](https://github.com/integratorjeffj/foreman-command-center)** · Construction program command center
**[Plumbline](https://github.com/integratorjeffj/plumbline)** · Bid leveling for estimators
**[AI Arcade Academy](https://github.com/integratorjeffj/arcade-academy)** · Browser-based AI literacy game

Jeff Jenkins · [GitHub](https://github.com/integratorjeffj) · [LinkedIn](https://www.linkedin.com/in/jeffjenkins3418)
