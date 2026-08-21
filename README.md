# Imprint

**Licensing stamper for small sheet music publishers.**

Stamps every page of a PDF with the buyer's name, locks the file against
editing, and records the copy in a permanent ledger. Reads a PayPal export and
processes a day of orders in one pass.

**→ [How it works](https://integratorjeffj.github.io/opus) · [Sample output](samples/licensed)**

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

## What Imprint does

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

## Running it

```bash
pip3 install pypdf reportlab pikepdf
python3 imprint.py
```

That opens the app window, which is how it is meant to be used day to day. The
command line is there for scripting and testing:

```bash
# Build a catalog map by scanning a folder of pieces
python3 imprint.py --make-catalog ./catalog -o catalog_map.csv

# See what a PayPal export would produce, without writing anything
python3 imprint.py --paypal examples/paypal_sample.csv \
    --catalog examples/catalog_map.csv --out ./licensed --dry-run

# One-off license outside of PayPal
python3 imprint.py --licensee "Grace Chapel" --out ./licensed \
    --folder "./catalog/Evening Bells"
```

`qpdf` is optional. Without it the extra form-flatten pass is skipped, which is
fine for engraved music that has no form fields.

## Try it

`examples/paypal_sample.csv` is a deliberately messy export: two title
variants for the same piece, a refund, a general withdrawal, a pending payment,
and an order for a piece that isn't in the catalog. Run the dry-run command
above and every one of those is handled or flagged.

`samples/` holds the fictional catalog and the licensed output it produces, so
you can open a stamped PDF without installing anything.

## Roadmap

- [ ] Auto-email delivery with the send recorded in the ledger
- [ ] Watch folder, so dropping an export in runs the batch on a schedule
- [ ] Packaged `.app` and `.exe` via PyInstaller, removing the Python install
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
