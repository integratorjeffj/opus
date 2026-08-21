"""Sockets that exist as a contract and nothing more.

These are registered on purpose. Showing the shape of what Opus will plug into
is honest product storytelling -- a publisher can see that Dropbox and Outlook
are a named, planned adapter rather than a hope -- and it costs nothing to keep
the roadmap in the same place as the code, where it cannot drift from it.

What they must never do is pretend. Every one of these raises when called, with
a message saying it is not built. There is no mock sign-in, no fake Connect
button, no silently-empty result that looks like "no orders today". A greyed
tile marked Planned reads as a plan; a flow that looks live until you press the
last button reads as a lie, and costs more trust than the feature would win.
"""

from .base import (PLANNED, CatalogSource, ConnectorError, DeliveryChannel,
                   OrderSource, register)


class _NotBuilt:
    """Shared refusal. Says what is missing and where it sits on the roadmap."""

    phase = "a later phase"

    @classmethod
    def _refuse(cls):
        raise ConnectorError(
            "{} is not built yet -- it is planned for {}. Nothing was read or "
            "sent. Use a connector marked 'built' instead."
            .format(cls.label, cls.phase))

    def health(self):
        return False, "Planned for {}. Not built.".format(self.phase)


# ---------------------------------------------------------------------------
# Order sources
# ---------------------------------------------------------------------------

@register
class StripeOrders(_NotBuilt, OrderSource):
    name = "stripe"
    label = "Stripe"
    description = "Completed Stripe payments."
    state = PLANNED
    phase = "after PayPal is proven in daily use"

    def list_orders(self, since=None):
        self._refuse()


@register
class SquareOrders(_NotBuilt, OrderSource):
    name = "square"
    label = "Square"
    description = "Completed Square payments."
    state = PLANNED
    phase = "after PayPal is proven in daily use"

    def list_orders(self, since=None):
        self._refuse()


@register
class MailboxOrders(_NotBuilt, OrderSource):
    name = "mailbox"
    label = "Mailbox receipts"
    description = "Order confirmations parsed out of a Gmail or Outlook inbox."
    state = PLANNED
    phase = "phase 4, once the confidence score decides what needs review"

    def list_orders(self, since=None):
        self._refuse()


# ---------------------------------------------------------------------------
# Catalog sources
# ---------------------------------------------------------------------------

@register
class DropboxCatalog(_NotBuilt, CatalogSource):
    name = "dropbox"
    label = "Dropbox folder"
    description = "A shared Dropbox folder, one subfolder per piece."
    state = PLANNED
    phase = "phase 2, after Drive"

    def list_items(self):
        self._refuse()

    def materialize(self, dest):
        self._refuse()


@register
class OneDriveCatalog(_NotBuilt, CatalogSource):
    name = "onedrive"
    label = "OneDrive / SharePoint"
    description = ("A OneDrive or SharePoint library. Needs an Azure app "
                   "registration and a tenant admin's consent.")
    state = PLANNED
    phase = "phase 2, after Drive"

    def list_items(self):
        self._refuse()

    def materialize(self, dest):
        self._refuse()


# ---------------------------------------------------------------------------
# Delivery channels -- the whole kind is Phase 3
# ---------------------------------------------------------------------------

@register
class PortalDelivery(_NotBuilt, DeliveryChannel):
    name = "portal"
    label = "Expiring download link"
    description = ("A per-order link that expires. Preferred over attachments: "
                   "no size limit, better deliverability, and download "
                   "telemetry that is itself a licensing signal.")
    state = PLANNED
    phase = "phase 3"

    def deliver(self, order, files):
        self._refuse()


@register
class SMTPDelivery(_NotBuilt, DeliveryChannel):
    name = "smtp"
    label = "Plain SMTP"
    description = "Send through any mail server with a username and password."
    state = PLANNED
    phase = "phase 3"

    def deliver(self, order, files):
        self._refuse()


@register
class GmailDelivery(_NotBuilt, DeliveryChannel):
    name = "gmail"
    label = "Gmail / Google Workspace"
    description = "Send as the publisher's own Gmail address."
    state = PLANNED
    phase = "phase 3"

    def deliver(self, order, files):
        self._refuse()


@register
class GraphDelivery(_NotBuilt, DeliveryChannel):
    name = "outlook"
    label = "Outlook / Microsoft 365"
    description = "Send through Microsoft Graph. Needs tenant admin consent."
    state = PLANNED
    phase = "phase 3"

    def deliver(self, order, files):
        self._refuse()
