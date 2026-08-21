# Sample catalog and licensed output

Everything in this folder is fictional and safe to open, copy, or re-run
against. It exists so you can see what Opus produces without installing
anything, and so you never need a real PayPal export to try it.

## `catalog/`

Two pieces by "A. Fictitious Composer", six PDFs in all, engraved for this
demo and marked as a demo on every page:

| Piece | Parts | Pages |
|---|---|---|
| Evening Bells | `score.pdf`, `choral_score.pdf`, `organ.pdf` | 3, 2, 2 |
| Fanfare for Two Trumpets | `score.pdf`, `trumpet_1.pdf`, `trumpet_2.pdf` | 2, 1, 1 |

This is what a publisher's source folder looks like: one folder per piece,
several PDFs inside, and `score.pdf` appearing in both of them — which is
exactly why the output filename carries the piece name too.

`examples/catalog_map.csv` points here.

## `licensed/`

The output of a real (not dry) run against `examples/paypal_sample.csv`:

```bash
python3 opus.py --paypal examples/paypal_sample.csv \
    --catalog examples/catalog_map.csv --out samples/licensed --demo-ack
```

Nine stamped files for three orders, plus `license_ledger.csv` recording every
one. Open any of them: the licensee's notice runs across the top of every page,
the purchase date across the bottom, and the file will not let you edit or
extract from it. It opens and prints with no password.

The fourth order in the export — `Processional in D` — is not in the catalog,
so it was flagged rather than guessed at, and produced nothing. The refund, the
withdrawal and the pending payment never reached the plan at all.

## The ledger now carries decisions and a chain

Each row records what the confidence engine concluded (`confidence`,
`decision`) alongside what was stamped, and commits to the row before it
(`prev_hash`, `row_hash`). Check it:

```bash
python3 opus.py --verify-ledger samples/licensed/license_ledger.csv
```

The `delivery_*` columns are empty here, because nothing was actually sent --
these files were produced by a local run with no delivery channel configured.

## Two notes on the committed ledger

The `source_file` and `output_file` columns were rewritten from absolute paths
to repo-relative ones before publishing, so the file does not carry a local
machine's directory layout. Because those columns are covered by the hash, the
chain was recomputed afterwards -- so it verifies, but it attests to the
published rows rather than to the original run. Nothing else was changed.

Every row's `notes` reads `qpdf not installed; flatten step skipped`. That is
the tool being honest rather than a failure: `qpdf` is an optional extra pass
that flattens form fields, and engraved music has none to flatten. The stamping,
the AES-256 lock and the ledger row all happened normally.

## On the ledger's `owner_password` column

Those values are real AES-256 owner passwords for these fictional PDFs, and
they are committed on purpose so the ledger reads as a genuine artifact. They
unlock nothing outside this folder.

In a live deployment the ledger is not a public file. It is the record that ties
a leaked copy back to an order, and it belongs wherever the publisher keeps
their business records.
