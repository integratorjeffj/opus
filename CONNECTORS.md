# Connectors

Gmail, Drive, Outlook, OneDrive, Dropbox, Stripe and Square look like a long
list of integrations to build. They are not. They are three kinds of thing:

| Socket | Contract | What plugs in |
|---|---|---|
| **Order source** | `list_orders(since)` | PayPal, Stripe, Square, a mailbox |
| **Catalog source** | `list_items()`, `materialize(dest)` | a folder, Drive, Dropbox, OneDrive |
| **Delivery channel** | `deliver(order, files)` | a portal link, SMTP, Gmail, Outlook |

The engine never learns what Dropbox is. It asks a catalog source for a local
folder and hands finished files to a delivery channel. Adding Box later is a
new adapter against an existing contract, not a rewrite.

```bash
python3 opus.py --list-connectors
```

## What is actually built

| | Connector | State |
|---|---|---|
| Order | `paypal-csv` — a downloaded PayPal activity export | **built** |
| Order | `paypal-api` — PayPal Transaction Search API | **unverified** |
| Catalog | `local` — a folder on this machine | **built** |
| Catalog | `gdrive` — a shared Google Drive folder | **unverified** |
| Order | `stripe`, `square`, `mailbox` | planned |
| Catalog | `dropbox`, `onedrive` | planned |
| Delivery | `portal` — expiring download link | **built** |
| Delivery | `smtp` — any mail server | **built** |
| Delivery | `gmail`, `outlook` | planned |

Three states, and the distinction is the point:

- **built** — implemented and covered by the test suite.
- **unverified** — implemented against the documented API and unit-tested
  against recorded responses, but **never run against the live service**. The
  response parsing is covered; the HTTP handshake is not. Treat the first live
  run as a test.
- **planned** — a contract and nothing else. Selecting one is an *error*, not a
  quiet no-op. There is no mock sign-in and no empty result that could be
  mistaken for "no orders today".

That last rule is deliberate. A greyed tile marked planned reads as a plan; a
flow that looks live until the final button reads as a lie, and costs more
trust than the feature would have won.

## Using them

The old flags still work and still mean what they did:

```bash
python3 opus.py --paypal export.csv --catalog catalog_map.csv --out ./licensed
```

The connector form is explicit about where things come from:

```bash
# A folder of pieces, no catalog_map.csv to maintain by hand
python3 opus.py --order-source paypal-csv --paypal export.csv \
    --catalog-source local --catalog-root ./catalog \
    --out ./licensed --dry-run

# Only orders from a date forward
python3 opus.py --order-source paypal-csv --paypal export.csv \
    --catalog-source local --catalog-root ./catalog \
    --since 8/19/26 --out ./licensed --dry-run
```

`--catalog-source` builds the catalog map for you by scanning the folder, so
the map stops being a file anyone has to maintain.

## PayPal, live

Create a REST app under **Apps & Credentials** in the PayPal developer
dashboard, and enable the **Transaction Search** permission on it — the
credentials work without it, but every query comes back empty, which looks
exactly like a quiet day.

```bash
python3 opus.py --order-source paypal-api \
    --paypal-client-id "$PAYPAL_ID" --paypal-client-secret "$PAYPAL_SECRET" \
    --catalog-source local --catalog-root ./catalog \
    --since 8/1/26 --out ./licensed --dry-run
```

Two constraints come from PayPal and shape the adapter:

- A query may span at most **31 days**; wider ranges are chunked automatically.
- A transaction becomes visible to this API roughly **three hours** after it
  settles, so "today" is usually incomplete.

Re-running over an overlapping window is therefore normal and safe: the ledger
already skips transaction ids it has issued before. That duplicate guard is
what makes polling viable at all.

Add `--paypal-sandbox` to point at sandbox credentials.

## Google Drive, and why there is no "Sign in with Google"

The obvious design is an OAuth consent screen. It is the wrong one here.

Reading an existing Drive folder needs the `drive.readonly` scope, and that is
a **restricted** scope. Shipping an app that asks for it means Google's
verification review, and restricted scopes can additionally require a
third-party security assessment — weeks of calendar time and real money, so
that one business can read one folder.

A **service account** avoids all of it. Google issues a robot account with its
own email address; the publisher shares the catalogue folder with that address
exactly as they would with a colleague. No consent screen, no verification, no
refresh tokens to expire, and access limited to precisely the folders they
chose to share — which is *stricter* than the OAuth flow would have been, not
looser. Revoking access is un-sharing the folder.

The cost is a JSON key file, and it is a real credential. Keep it out of the
repository, out of email, and off shared drives.

**Setup**

1. Google Cloud console → create a project → enable the **Google Drive API**.
2. **Service Accounts** → create one → **Keys** → **Add key** → **JSON**.
3. Copy the service account's email (`…@….iam.gserviceaccount.com`).
4. In Drive, share the catalogue folder with that address, **Viewer** is enough.
5. Copy the folder's URL or id.

```bash
python3 opus.py --order-source paypal-csv --paypal export.csv \
    --catalog-source gdrive \
    --gdrive-key ./service-account.json \
    --gdrive-folder "https://drive.google.com/drive/folders/1AbC..." \
    --catalog-cache ./.opus-catalog \
    --out ./licensed --dry-run
```

The folder is synced into `--catalog-cache` and then treated as an ordinary
local catalogue. Files already present at the same byte size are skipped, so a
second sync is nearly free — and a synced catalogue keeps working with no
network at all.

Shared Drives are supported; a plain "Shared with me" folder works too, as long
as it is shared with the service account rather than with a person.

## Watched folder

```bash
# Watch for exports dropped into a folder, planning each one as it lands
python3 opus.py --watch ~/Dropbox/paypal-exports \
    --catalog-source local --catalog-root ./catalog --out ./licensed

# One pass and exit -- what a scheduled task wants
python3 opus.py --watch ~/Dropbox/paypal-exports --watch-once \
    --catalog-source local --catalog-root ./catalog --out ./licensed
```

**It plans. It does not stamp.** A human still approves the batch. That is the
Stage 2 boundary, and moving it is a later decision made on evidence rather
than on how well the watcher seems to be doing.

Two details that matter in practice:

- It **polls** rather than using filesystem events, because the watched folder
  is usually a synced one. Dropbox and Drive emit events that do not mean what
  they appear to — a file arrives at zero bytes and fills in afterwards, or is
  written under a temporary name and swapped. Waiting for a file whose size has
  stopped changing sidesteps that whole class of bug.
- It remembers what it has already seen in a `.opus-seen` file, keyed on name
  *and* size — so a re-uploaded export with the same name but new contents is
  correctly treated as new.

For unattended running, `--watch-once` from cron or Task Scheduler every
fifteen minutes is usually a better answer than a daemon.

## Writing a new adapter

Subclass the contract, set the identity fields, and register it:

```python
from connectors.base import BUILT, OrderSource, make_order, register

@register
class MyShop(OrderSource):
    name = "myshop"
    label = "My Shop"
    description = "Completed orders from My Shop."
    state = BUILT

    def configure(self, api_key=None, **_ignored):
        self.api_key = api_key
        return self

    def health(self):
        return bool(self.api_key), "ready" if self.api_key else "No API key."

    def list_orders(self, since=None):
        return [make_order(buyer="...", item_title="...",
                           order_ref="...", order_date=..., email="...")], []
```

Import it in `connectors/__init__.py` so registration happens, and it appears
in `--list-connectors` and the Connections tab with no further work.

Three rules the existing adapters follow:

1. **Filter non-sales at the source.** Refunds, reversals, withdrawals and
   pending rows must never leave an adapter. The engine should not have to know
   which providers call a refund what.
2. **Use `make_order`.** It produces the exact dict shape the engine consumes,
   so a future field rename lands in one place. `validate_orders` will catch a
   missing field at the seam rather than three steps later inside the planner.
3. **Be honest in `state`.** If it has never run against the live service, it is
   `unverified`, not `built`.

## Tests

```bash
python3 tests/test_connectors.py
```

84 checks, no pytest required. `tests/test_phase3.py` adds 80 more for
scoring, delivery and ledger integrity. The network adapters are tested against recorded
payloads in `tests/fixtures/` — which covers the transformation most likely to
drift when a provider renames a field, and leaves the HTTP handshake untested.
That gap is stated rather than papered over with a green tick.

## Delivery

The expiring portal and SMTP are built. See **[OVERSIGHT.md](OVERSIGHT.md)** for how they work together, why the email carries a link rather than the files, and what the bundled portal server is and is not.
