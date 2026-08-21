"""The three sockets Opus plugs into, and the registry that lists them.

Gmail, Drive, Outlook, OneDrive, Dropbox, Stripe and Square look like a long
list of integrations. They are not. They are three kinds of thing:

    OrderSource      where orders come from
    CatalogSource    where the master PDFs live
    DeliveryChannel  how the finished files reach the buyer

Each kind has one contract. The engine never learns what Dropbox is -- it asks
a CatalogSource for a local folder and hands finished files to a
DeliveryChannel. Adding Box later is a new adapter, not a rewrite.

DATA SHAPES
    An order is a plain dict, deliberately the same shape the engine has always
    consumed, so adapters could be introduced without touching the matching,
    planning or stamping code:

        {"buyer": str, "item_title": str, "order_ref": str,
         "order_date": datetime.date, "email": str}

    A catalog is materialised to a local folder plus a catalog_map.csv, which
    is what load_catalog() already reads. Remote sources download first and
    then look exactly like a local one, which also means a synced catalogue
    keeps working with no network.
"""

import abc
from datetime import date


# ---------------------------------------------------------------------------
# Availability -- what the connector gallery is allowed to claim
# ---------------------------------------------------------------------------

BUILT = "built"          # implemented and exercised by the test suite
UNVERIFIED = "unverified"  # implemented, but never run against the live service
PLANNED = "planned"      # a name and a contract, no implementation

STATES = (BUILT, UNVERIFIED, PLANNED)


class ConnectorError(Exception):
    """Anything an adapter could not do. Carries a message fit to show a user."""


class NotConfigured(ConnectorError):
    """The adapter needs credentials or settings it has not been given."""


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

class CatalogItem:
    """One sellable piece: a title and the files that make it up."""

    __slots__ = ("title", "ref", "file_count", "notes")

    def __init__(self, title, ref="", file_count=0, notes=""):
        self.title = title
        self.ref = ref                # source-native id (Drive folder id, path)
        self.file_count = file_count
        self.notes = notes

    def __repr__(self):
        return "CatalogItem({!r}, {} file(s))".format(self.title, self.file_count)


def make_order(buyer, item_title, order_ref, order_date=None, email=""):
    """Build an order dict in the shape the engine expects.

    Adapters should go through this rather than assembling dicts by hand, so a
    field rename lands in one place.
    """
    return {
        "buyer": (buyer or "").strip(),
        "item_title": (item_title or "").strip(),
        "order_ref": (order_ref or "").strip(),
        "order_date": order_date or date.today(),
        "email": (email or "").strip(),
    }


REQUIRED_ORDER_FIELDS = ("buyer", "item_title", "order_ref", "order_date", "email")


def validate_orders(orders):
    """Return a list of complaints about orders an adapter produced.

    Cheap insurance: a new adapter that forgets a field is caught at the seam
    rather than three steps later inside the planner.
    """
    problems = []
    for i, o in enumerate(orders):
        if not isinstance(o, dict):
            problems.append("order {} is {}, not a dict".format(i, type(o).__name__))
            continue
        for f in REQUIRED_ORDER_FIELDS:
            if f not in o:
                problems.append("order {} is missing '{}'".format(i, f))
        if "order_date" in o and not isinstance(o["order_date"], date):
            problems.append("order {} has a non-date order_date: {!r}"
                            .format(i, o["order_date"]))
    return problems


# ---------------------------------------------------------------------------
# The three contracts
# ---------------------------------------------------------------------------

class Connector(abc.ABC):
    """Shared identity. Subclasses set these as class attributes."""

    name = ""            # stable id used on the command line
    label = ""           # what a person sees
    description = ""
    state = PLANNED

    def configure(self, **settings):
        """Accept credentials or paths. Returns self so calls can chain."""
        return self

    def health(self):
        """(ok, message) -- can this adapter actually be used right now?

        Never raises. The connector gallery calls it to show a status, so a
        misconfigured adapter must report rather than explode.
        """
        return True, "ready"


class OrderSource(Connector):
    """Where orders come from."""

    @abc.abstractmethod
    def list_orders(self, since=None):
        """Return (orders, warnings).

        `since` is a date or None. Adapters should filter server-side where the
        API allows it and client-side otherwise. Rows that are not completed
        sales -- refunds, reversals, withdrawals, pending -- must be dropped
        here, not passed on for the engine to worry about.
        """


class CatalogSource(Connector):
    """Where the master PDFs live."""

    @abc.abstractmethod
    def list_items(self):
        """Return a list of CatalogItem, without downloading anything."""

    @abc.abstractmethod
    def materialize(self, dest):
        """Make the catalogue available as a local folder. Returns its Path.

        A local source returns the folder it already has. A remote source
        downloads into `dest` and returns that -- after which it is
        indistinguishable from a local one, which is what lets the engine stay
        ignorant of where files came from.
        """

    def catalog_map(self, dest, map_path=None):
        """Write a catalog_map.csv describing the materialised catalogue.

        Returns its Path. `map_path` puts the generated map somewhere other
        than inside the catalogue -- which matters for a local source, because
        the catalogue is the publisher's own master folder and very often a
        synced one. Dropping a generated file into it would sync to everyone
        and show up as a change they did not make.
        """
        from .catalog_local import write_catalog_map
        root = self.materialize(dest)
        return write_catalog_map(root, map_path)


class DeliveryChannel(Connector):
    """How finished files reach the buyer. Phase 3 -- contract only for now."""

    @abc.abstractmethod
    def deliver(self, order, files):
        """Send `files` for `order`. Returns a receipt dict.

        A receipt must carry at least {"channel": name, "sent_at": iso string,
        "detail": str} so the ledger can record how a copy actually left.
        """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY = {"order": {}, "catalog": {}, "delivery": {}}

_KIND_OF = [
    (OrderSource, "order"),
    (CatalogSource, "catalog"),
    (DeliveryChannel, "delivery"),
]


def register(cls):
    """Class decorator. Adds an adapter to the registry under its kind."""
    for base_cls, kind in _KIND_OF:
        if issubclass(cls, base_cls):
            if not cls.name:
                raise ValueError("{} needs a name".format(cls.__name__))
            if cls.state not in STATES:
                raise ValueError("{} has an unknown state {!r}"
                                 .format(cls.__name__, cls.state))
            _REGISTRY[kind][cls.name] = cls
            return cls
    raise TypeError("{} does not implement any connector contract"
                    .format(cls.__name__))


def get(kind, name):
    """Look up an adapter class. Raises ConnectorError with the valid names."""
    try:
        return _REGISTRY[kind][name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY.get(kind, {}))) or "none"
        raise ConnectorError("No {} connector called {!r}. Available: {}"
                             .format(kind, name, known))


def available(kind=None):
    """Every registered adapter class, optionally for one kind."""
    if kind:
        return dict(_REGISTRY[kind])
    return {k: dict(v) for k, v in _REGISTRY.items()}


def describe():
    """Rows for the connector gallery: (kind, name, label, state, description).

    Sorted so built adapters lead, because that is the order a person wants to
    read them in.
    """
    order = {BUILT: 0, UNVERIFIED: 1, PLANNED: 2}
    rows = []
    for kind, adapters in _REGISTRY.items():
        for name, cls in adapters.items():
            rows.append((kind, name, cls.label, cls.state, cls.description))
    rows.sort(key=lambda r: (r[0], order.get(r[3], 9), r[1]))
    return rows
