"""Orders straight from PayPal, no downloaded CSV in the middle.

Uses the Transaction Search API with an OAuth2 client-credentials token. Two
constraints from PayPal shape the code:

  * a request may span at most 31 days, so a wider window is chunked;
  * transactions only become visible to this API around three hours after they
    settle, so "today" is usually incomplete. The engine's duplicate guard --
    transaction ids already in the ledger are skipped -- is what makes it safe
    to re-run over an overlapping window, which is exactly what you want here.

STATUS: implemented against the documented API and unit-tested against recorded
responses, but never run against a live PayPal account. The response parsing is
covered; the HTTP handshake is not. Treat the first live run as a test.
"""

from datetime import date, datetime, timedelta, timezone

from .base import (UNVERIFIED, ConnectorError, NotConfigured, OrderSource,
                   make_order, register)
from .http import request

LIVE_BASE = "https://api-m.paypal.com"
SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

MAX_WINDOW_DAYS = 31
PAGE_SIZE = 500

# PayPal's per-transaction status. Only S is money that actually arrived.
STATUS_SUCCESS = "S"

# Event codes that are not a sale, even when they carry a positive amount.
# T11xx is the refund/reversal family, T04xx withdrawals, T12xx chargebacks.
NON_SALE_PREFIXES = ("T11", "T04", "T12", "T15", "T16", "T19", "T20", "T21")


def _iso(d):
    """PayPal wants RFC 3339 with an offset."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


def _windows(start, end):
    """Split a date range into <=31-day chunks."""
    out = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def parse_transactions(payload):
    """Turn one Transaction Search page into (orders, warnings).

    Split out from the HTTP call so it can be tested against recorded
    responses, which is the part most likely to drift when PayPal changes a
    field name.
    """
    orders, warnings = [], []
    for entry in payload.get("transaction_details") or []:
        info = entry.get("transaction_info") or {}

        if (info.get("transaction_status") or "").upper() != STATUS_SUCCESS:
            continue

        code = (info.get("transaction_event_code") or "").upper()
        if any(code.startswith(p) for p in NON_SALE_PREFIXES):
            continue

        amount = info.get("transaction_amount") or {}
        try:
            value = float(amount.get("value", "0") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value <= 0:            # refunds and withdrawals arrive negative
            continue

        payer = entry.get("payer_info") or {}
        name = (payer.get("payer_name") or {})
        buyer = (name.get("alternate_full_name")
                 or " ".join(p for p in (name.get("given_name"),
                                         name.get("surname")) if p)
                 or payer.get("email_address") or "").strip()

        items = ((entry.get("cart_info") or {}).get("item_details")) or []
        title = ""
        for item in items:
            title = (item.get("item_name") or "").strip()
            if title:
                break
        if not title:
            title = (info.get("transaction_subject")
                     or info.get("transaction_note") or "").strip()

        txn = (info.get("transaction_id") or "").strip()

        if not buyer or not title:
            if txn:
                warnings.append(
                    "Transaction {} has no {} and was skipped."
                    .format(txn, "buyer name" if not buyer else "item title"))
            continue

        raw = info.get("transaction_initiation_date") or ""
        try:
            order_date = datetime.fromisoformat(
                raw.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            order_date = date.today()
            warnings.append("Unreadable date on {}; used today.".format(txn))

        orders.append(make_order(buyer=buyer, item_title=title, order_ref=txn,
                                 order_date=order_date,
                                 email=payer.get("email_address", "")))
    return orders, warnings


@register
class PayPalAPI(OrderSource):
    name = "paypal-api"
    label = "PayPal (live account)"
    description = ("Reads completed payments directly from PayPal's "
                   "Transaction Search API.")
    state = UNVERIFIED

    def __init__(self, client_id=None, client_secret=None, sandbox=False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base = SANDBOX_BASE if sandbox else LIVE_BASE
        self._token = None

    def configure(self, client_id=None, client_secret=None, sandbox=None,
                  **_ignored):
        if client_id:
            self.client_id = client_id
        if client_secret:
            self.client_secret = client_secret
        if sandbox is not None:
            self.base = SANDBOX_BASE if sandbox else LIVE_BASE
        self._token = None
        return self

    # -- auth ---------------------------------------------------------------

    def _access_token(self):
        if self._token:
            return self._token
        if not (self.client_id and self.client_secret):
            raise NotConfigured(
                "PayPal needs a client ID and secret. Create them under "
                "Apps & Credentials in the PayPal developer dashboard, and "
                "enable the Transaction Search permission on the app.")
        import base64
        cred = base64.b64encode(
            "{}:{}".format(self.client_id, self.client_secret).encode()).decode()
        payload = request(
            self.base + "/v1/oauth2/token", method="POST",
            headers={"Authorization": "Basic " + cred,
                     "Content-Type": "application/x-www-form-urlencoded"},
            body="grant_type=client_credentials")
        self._token = payload.get("access_token")
        if not self._token:
            raise ConnectorError("PayPal did not return an access token.")
        return self._token

    def _headers(self):
        return {"Authorization": "Bearer " + self._access_token(),
                "Content-Type": "application/json"}

    # -- contract -----------------------------------------------------------

    def health(self):
        if not (self.client_id and self.client_secret):
            return False, "No PayPal client ID and secret set."
        try:
            self._access_token()
        except ConnectorError as exc:
            return False, str(exc)
        return True, "Signed in to {}".format(
            "sandbox" if self.base == SANDBOX_BASE else "live PayPal")

    def list_orders(self, since=None):
        start = since or (date.today() - timedelta(days=7))
        end = date.today()
        if start > end:
            return [], ["Start date {} is in the future.".format(start)]

        orders, warnings = [], []
        seen = set()

        for win_start, win_end in _windows(start, end):
            page = 1
            while True:
                payload = request(
                    self.base + "/v1/reporting/transactions",
                    headers=self._headers(),
                    params={"start_date": _iso(win_start),
                            "end_date": _iso(win_end + timedelta(days=1)),
                            "fields": "transaction_info,payer_info,cart_info",
                            "page_size": PAGE_SIZE, "page": page})
                got, warn = parse_transactions(payload)
                warnings.extend(warn)
                for o in got:
                    # Overlapping windows and paging can repeat a transaction.
                    if o["order_ref"] and o["order_ref"] in seen:
                        continue
                    seen.add(o["order_ref"])
                    orders.append(o)

                total_pages = int(payload.get("total_pages") or 1)
                if page >= total_pages:
                    break
                page += 1

        if not orders:
            warnings.append(
                "No completed payments found between {} and {}. Note that "
                "PayPal can take a few hours to make a transaction visible to "
                "this API.".format(start.isoformat(), end.isoformat()))
        return orders, warnings
