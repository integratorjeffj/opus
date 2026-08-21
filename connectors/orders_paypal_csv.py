"""Orders from a downloaded PayPal activity export.

The parsing lives in opus.py and has been in use since before connectors
existed. This wraps it rather than reimplementing it: the CSV reader is the
most battle-tested part of the system -- it knows that PayPal has renamed its
export headers several times, and it knows which row types are not sales -- and
duplicating that logic to satisfy an interface would be a poor trade.
"""

from pathlib import Path

from .base import BUILT, ConnectorError, OrderSource, register


def _engine():
    """Import opus lazily.

    opus.py imports this package, so importing it at module scope would be
    circular. Deferring to call time keeps both directions working.
    """
    import opus
    return opus


@register
class PayPalCSV(OrderSource):
    name = "paypal-csv"
    label = "PayPal activity export"
    description = "A CSV downloaded from PayPal's Activity page."
    state = BUILT

    def __init__(self, path=None):
        self.path = Path(path) if path else None

    def configure(self, path=None, **_ignored):
        if path:
            self.path = Path(path)
        return self

    def health(self):
        if not self.path:
            return False, "No PayPal CSV chosen."
        if not self.path.is_file():
            return False, "File not found: {}".format(self.path)
        return True, str(self.path)

    def list_orders(self, since=None):
        if not self.path:
            raise ConnectorError("No PayPal CSV chosen.")
        if not self.path.is_file():
            raise ConnectorError("PayPal CSV not found: {}".format(self.path))

        orders, warnings = _engine().read_paypal_orders(self.path)

        if since:
            kept = [o for o in orders if o["order_date"] >= since]
            dropped = len(orders) - len(kept)
            if dropped:
                warnings.append(
                    "{} order(s) before {} were skipped.".format(
                        dropped, since.isoformat()))
            orders = kept
        return orders, warnings
