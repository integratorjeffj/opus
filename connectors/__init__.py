"""Opus connectors: the three sockets, and every adapter plugged into them.

    from connectors import get, describe
    source = get("order", "paypal-csv")().configure(path="activity.csv")
    orders, warnings = source.list_orders()

Importing this package registers every adapter, which is what makes the
connector gallery able to list what exists without the GUI knowing any of their
names.
"""

from .base import (BUILT, PLANNED, UNVERIFIED, CatalogItem, CatalogSource,
                   Connector, ConnectorError, DeliveryChannel, NotConfigured,
                   OrderSource, available, describe, get, make_order, register,
                   validate_orders)

# Imported for their side effect: each module registers its adapters.
from . import catalog_gdrive          # noqa: F401
from . import catalog_local           # noqa: F401
from . import orders_paypal_api       # noqa: F401
from . import orders_paypal_csv       # noqa: F401
from . import planned                 # noqa: F401
from .watch import WatchedFolder, watch

__all__ = [
    "BUILT", "UNVERIFIED", "PLANNED",
    "CatalogItem", "CatalogSource", "Connector", "ConnectorError",
    "DeliveryChannel", "NotConfigured", "OrderSource",
    "available", "describe", "get", "make_order", "register",
    "validate_orders", "WatchedFolder", "watch",
]
