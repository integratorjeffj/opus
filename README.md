# Opus

**Licensing stamper for small sheet music publishers.**

Stamps every page of a PDF with the buyer's name, locks the file against
editing, and records the copy in a permanent ledger. Reads a PayPal export and
processes a day of orders in one pass.

**→ [How it works](https://integratorjeffj.github.io/opus) · [Sample output](samples/licensed)**

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

## Roadmap

- [ ] Auto-email delivery with the send recorded in the ledger
- [ ] Watch folder, so dropping an export in runs the batch on a schedule
- [x] Packaged `.app` and `.exe` via PyInstaller, removing the Python install
- [ ] Code signing and notarization, so a first run is not a warning
- [ ] Per-page notice placement rules for pieces with tight title blocks
- [ ] Shopify and Square export adapters alongside PayPal

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
