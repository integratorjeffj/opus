# Example inputs

Fictional data for trying Opus without touching real orders. Everything in
this folder is invented: the buyers, the email addresses, the transaction IDs
and the pieces. Use it -- or `../samples/catalog/` -- instead of a live PayPal
export. Opus asks you to confirm you are doing exactly that before it writes
anything.

**`paypal_sample.csv`** — a deliberately messy PayPal activity export:

- Two different title strings for the same piece (`Evening Bells - PDF Download` and `EVENING BELLS (score & parts)`)
- A refund row, which must not be processed as a sale
- A general withdrawal with no item title
- A pending payment, which is not yet a completed sale
- An order for `Processional in D`, which is not in the catalog

**`catalog_map.csv`** — maps each item title to the folder holding its PDFs. Paths are relative to this file, so the examples work from a fresh clone.

## Try it

```bash
python3 opus.py --paypal examples/paypal_sample.csv \
    --catalog examples/catalog_map.csv --out ./licensed --dry-run
```

`--dry-run` writes nothing, so it runs without the demo-data prompt. A real
run asks first, or takes `--demo-ack`.

Expected: three orders ready (9 files), one flagged as `no catalog match`, and the refund, withdrawal and pending rows filtered out before they ever appear.
